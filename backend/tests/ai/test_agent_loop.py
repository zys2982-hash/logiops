"""有界循环单元测试：步数语义、超时、连续校验失败、白名单。"""

from __future__ import annotations

import pytest

from app.ai.agent import (
    MAX_LOOP_STEPS,
    MAX_VALIDATION_FAILURES,
    TOTAL_TIMEOUT_SECONDS,
    AgentState,
    StepRecorder,
    run_agent,
    t2_validator,
)
from app.ai.errors import AiOutputInvalid, AiUnavailable
from app.ai.providers import ProviderResult, ToolCall
from app.services import read_models
from tests.ai.fake_llm import ScriptedProvider, final_result, fixture_result, tool_turn


def _state(repos, case_a) -> AgentState:
    facts = read_models.exception_facts(repos, case_a["exception_id"])
    state = AgentState(
        task_type="ANALYZE_EXCEPTION",
        facts=facts,
        repos=repos,
        context={"exception_id": case_a["exception_id"]},
        input_hash="test-hash",
        system="sys",
        prompt="prompt",
    )
    state.evidence.add_facts(facts)
    return state


def _good_output() -> dict:
    return fixture_result("t2_analyze_exception_case_a.json").final


def test_loop_limits_are_frozen():
    # 14 = 1 次决策 LLM + 最多 7 个只读工具 + 最终输出 LLM + 少量重试余量
    # （原为 8：回放 fixture 只录了 3 个工具够用，但 live 模式下模型会查满 7 个工具而超限）
    assert MAX_LOOP_STEPS == 14
    assert TOTAL_TIMEOUT_SECONDS == 90
    assert MAX_VALIDATION_FAILURES == 2


def test_step_recorder_writes_sequential_steps(repos, case_a):
    recorder = StepRecorder(repos, case_a["analysis_id"])
    recorder.record("TOOL", tool_name="get_order", args={"order_id": 1}, result_summary="ok", duration_ms=3)
    recorder.record("VALIDATE", status="ERROR", error="不一致")
    steps = repos.analysis_steps.list_for_analysis(case_a["analysis_id"])
    assert [step.step_no for step in steps] == [1, 2]
    assert steps[0].args_json == {"order_id": 1}
    assert steps[1].status == "ERROR"
    assert recorder.written == 2


def test_recorder_is_noop_without_analysis(repos):
    recorder = StepRecorder(repos, None)
    assert recorder.enabled is False
    assert recorder.record("LLM", result_summary="x") == 0


def test_loop_records_llm_tool_llm_validate(repos, case_a):
    provider = ScriptedProvider(
        [
            tool_turn([ToolCall("get_order", {"order_id": case_a["order_id"]})]),
            final_result(_good_output()),
        ]
    )
    recorder = StepRecorder(repos, case_a["analysis_id"])
    result = run_agent(
        provider=provider,
        state=_state(repos, case_a),
        validate=t2_validator,
        recorder=recorder,
    )
    types = [step.step_type for step in repos.analysis_steps.list_for_analysis(case_a["analysis_id"])]
    assert types == ["LLM", "TOOL", "LLM", "VALIDATE"]
    assert result.output["impact"]["delay_minutes"] == case_a["delay_minutes"]
    assert result.steps_written == 4
    assert result.prompt_version == "v1"


def test_loop_repair_feedback_is_passed_back(repos, case_a):
    provider = ScriptedProvider(
        [
            ProviderResult(final=None, raw_text="不是 JSON"),
            ProviderResult(final=None, raw_text="仍然不是 JSON"),
        ]
    )
    with pytest.raises(AiOutputInvalid) as exc:
        run_agent(
            provider=provider,
            state=_state(repos, case_a),
            validate=t2_validator,
            recorder=StepRecorder(repos, case_a["analysis_id"]),
        )
    assert provider.calls[1]["repair_errors"]
    assert len(exc.value.details["errors"]) >= 1
    all_types = [step.step_type for step in repos.analysis_steps.list_for_analysis(case_a["analysis_id"])]
    assert all_types == ["LLM", "VALIDATE", "LLM", "VALIDATE"]


def test_loop_terminates_on_timeout():
    class FastClock:
        def __init__(self) -> None:
            self.value = 0.0

        def __call__(self) -> float:
            self.value += 1.0
            return self.value

    provider = ScriptedProvider([tool_turn([ToolCall("get_order", {"order_id": 1})]), final_result(_good_output())])
    state = AgentState(task_type="ANALYZE_EXCEPTION", prompt="x")
    with pytest.raises(AiUnavailable) as exc:
        run_agent(
            provider=provider,
            state=state,
            validate=t2_validator,
            timeout_seconds=0.0,
            monotonic=FastClock(),
        )
    assert "超时" in exc.value.message


def test_loop_rejects_non_whitelisted_tool(repos, case_a):
    provider = ScriptedProvider([tool_turn([ToolCall("drop_table", {})])])
    with pytest.raises(AiOutputInvalid) as exc:
        run_agent(
            provider=provider,
            state=_state(repos, case_a),
            validate=t2_validator,
            recorder=StepRecorder(repos, case_a["analysis_id"]),
        )
    assert "白名单" in exc.value.message
    steps = repos.analysis_steps.list_for_analysis(case_a["analysis_id"])
    assert steps[0].step_type == "LLM"
    assert steps[1].step_type == "TOOL" and steps[1].status == "ERROR"


def test_loop_tool_error_does_not_stop_validation(repos, case_a):
    provider = ScriptedProvider(
        [
            tool_turn([ToolCall("get_vehicle", {"vehicle_id": 999999})]),
            final_result(_good_output()),
        ]
    )
    recorder = StepRecorder(repos, case_a["analysis_id"])
    result = run_agent(provider=provider, state=_state(repos, case_a), validate=t2_validator, recorder=recorder)
    assert result.output["summary"]
    steps = repos.analysis_steps.list_for_analysis(case_a["analysis_id"])
    assert [step.status for step in steps] == ["OK", "ERROR", "OK", "OK"]


def test_loop_hard_stop_at_max_steps(repos, case_a):
    """连续请求工具直到预算耗尽 → 必须终止（不允许无限循环）。"""
    calls = [tool_turn([ToolCall("get_order", {"order_id": case_a["order_id"]})])] * MAX_LOOP_STEPS
    provider = ScriptedProvider(calls)
    with pytest.raises(AiOutputInvalid) as exc:
        run_agent(provider=provider, state=_state(repos, case_a), validate=t2_validator)
    assert "步数" in exc.value.message
    assert len(provider.calls) < MAX_LOOP_STEPS
