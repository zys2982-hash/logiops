"""三个入口的端到端行为：写库字段、降级路径、事实拦截、步数上限。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.ai import facts as F
from app.ai.errors import AiOutputInvalid, AiUnavailable
from app.ai.providers import ProviderResult, ToolCall
from app.ai.replay import ReplayProvider, ReplayStore
from app.ai.runner import (
    compute_t1_input_hash,
    compute_t2_input_hash,
    compute_t3_input_hash,
    draft_notice,
    execute_analysis,
    parse_message,
)
from app.services import read_models
from tests.ai.fake_llm import FIXTURE_DIR, ScriptedProvider, final_result, fixture_result, tool_turn


def _fixture_with_hash(tmp_path: Path, name: str, input_hash: str) -> Path:
    payload = json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))
    payload["input_hash"] = input_hash
    target = tmp_path / f"matching_{name}"
    target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return target


def _empty_dir(tmp_path: Path) -> Path:
    empty = tmp_path / "empty_replay"
    empty.mkdir(exist_ok=True)
    return empty


def _steps(repos, analysis_id: int):
    return repos.analysis_steps.list_for_analysis(analysis_id)


# --- T2 ---------------------------------------------------------------------
def test_execute_analysis_replays_hash_matched_fixture(db_session, repos, case_a, tmp_path):
    facts = read_models.exception_facts(repos, case_a["exception_id"])
    input_hash = compute_t2_input_hash(facts)
    _fixture_with_hash(tmp_path, "t2_analyze_exception_case_a.json", input_hash)

    result = execute_analysis(
        db_session,
        repos,
        case_a["analysis_id"],
        provider=ReplayProvider(ReplayStore(tmp_path)),
    )

    assert result["status"] == "READY"
    # 新模型：延误 270min（档位 2）+ VIP 1 = 3 → HIGH（车辆单才会是 1+客户等级，且没有 SLA）
    assert result["risk_level"] == "HIGH"
    assert result["output"]["impact"]["delay_minutes"] == case_a["delay_minutes"]
    assert result["output"]["impact"]["sla_breached"] is True
    assert result["output"]["root_cause"]["code"] == "VEHICLE_BREAKDOWN"

    analysis = repos.analyses.get(case_a["analysis_id"])
    assert analysis.status == "READY"
    assert analysis.model == "deepseek-chat"
    assert analysis.is_replay is True
    assert analysis.prompt_version == "v1"
    assert analysis.input_hash == input_hash
    assert analysis.risk_level_calculated == "HIGH"
    assert analysis.tokens_in == 1730 and analysis.tokens_out == 420
    assert analysis.latency_ms is not None and analysis.latency_ms >= 0
    assert analysis.started_at is not None and analysis.finished_at is not None
    assert analysis.output_json == result["output"]
    assert analysis.error_code is None

    steps = _steps(repos, analysis.id)
    assert [step.step_type for step in steps] == ["TOOL", "TOOL", "TOOL", "LLM", "VALIDATE"]
    assert [step.tool_name for step in steps if step.step_type == "TOOL"] == [
        "get_order",
        "get_tracking_events",
        "get_vehicle",
    ]
    assert steps[-1].status == "OK"
    assert all(step.duration_ms is not None for step in steps)


def test_execute_analysis_template_fallback_without_fixture(db_session, repos, case_a, tmp_path):
    result = execute_analysis(
        db_session,
        repos,
        case_a["analysis_id"],
        provider=ReplayProvider(ReplayStore(_empty_dir(tmp_path))),
    )

    assert result["status"] == "READY"
    assert result["risk_level"] == "HIGH"
    analysis = repos.analyses.get(case_a["analysis_id"])
    assert analysis.model == "template"
    assert analysis.is_replay is True
    assert analysis.output_json["impact"]["delay_minutes"] == case_a["delay_minutes"]

    steps = _steps(repos, analysis.id)
    tool_steps = [step for step in steps if step.step_type == "TOOL"]
    llm_steps = [step for step in steps if step.step_type == "LLM"]
    assert [step.tool_name for step in tool_steps] == [
        "get_order",
        "get_tracking_events",
        "get_customer_sla",
        "get_vehicle",
        "get_exception_history",
        # 每个风险因子各查一次知识库：CASE-A = 延误(+2) + VIP 客户(+1)
        "search_knowledge",
        "search_knowledge",
    ]
    assert len(tool_steps) + len(llm_steps) <= 8  # 有界循环（§11.3）
    assert steps[-1].step_type == "VALIDATE" and steps[-1].status == "OK"


def test_risk_level_comes_from_rules_not_from_llm(db_session, repos, case_a, tmp_path):
    output = fixture_result("t2_analyze_exception_case_a.json").final
    output["impact"]["affected_customer_level"] = "NORMAL"  # 模型试图淡化客户等级
    provider = ScriptedProvider([final_result(output)])
    result = execute_analysis(db_session, repos, case_a["analysis_id"], provider=provider)
    # LLM 把客户等级淡化成 NORMAL 也改不动规则定级：延误 270min 档位 2 + 事实上的 VIP 1 = 3 → HIGH
    assert result["risk_level"] == "HIGH"
    assert result["output"]["impact"]["affected_customer_level"] == "NORMAL"
    assert "level" not in result["output"]  # 等级字段不由 LLM 提供


def test_fake_eta_and_fake_evidence_are_intercepted(db_session, repos, case_a, tmp_path):
    """核心防幻觉演示：模型自行编造 ETA/延误分钟/来源 → 拦截并标 FAILED。"""
    bad = fixture_result("t2_invalid_facts_case_a.json")
    provider = ScriptedProvider([bad, bad])

    with pytest.raises(AiOutputInvalid) as exc:
        execute_analysis(db_session, repos, case_a["analysis_id"], provider=provider, timeout_seconds=90)

    assert "sla_delay_minutes" in exc.value.message
    assert "防编造来源" in exc.value.message
    analysis = repos.analyses.get(case_a["analysis_id"])
    assert analysis.status == "FAILED"
    assert analysis.error_code == "AI_OUTPUT_INVALID"
    assert analysis.output_json is None
    assert analysis.raw_output
    assert analysis.finished_at is not None
    steps = _steps(repos, analysis.id)
    validate_steps = [step for step in steps if step.step_type == "VALIDATE"]
    assert len(validate_steps) == 2  # 修复重试 1 次后仍失败
    assert all(step.status == "ERROR" for step in validate_steps)


def test_llm_unavailable_marks_failed_with_recognizable_error(db_session, repos, case_a, tmp_path):
    provider = ScriptedProvider([AiUnavailable("LLM 超时（60s）")])
    with pytest.raises(AiUnavailable):
        execute_analysis(db_session, repos, case_a["analysis_id"], provider=provider)
    analysis = repos.analyses.get(case_a["analysis_id"])
    assert analysis.status == "FAILED"
    assert analysis.error_code == "LLM_UNAVAILABLE"
    assert "超时" in (analysis.error_message or "")
    assert any(step.step_type == "LLM" and step.status == "ERROR" for step in _steps(repos, analysis.id))


def test_step_budget_terminates_loop(db_session, repos, case_a, tmp_path):
    from app.ai.agent import MAX_LOOP_STEPS

    call = ToolCall("get_order", {"order_id": case_a["order_id"]})
    # 多给几轮脚本：确保是"步数预算"终止循环，而不是脚本回合不够
    provider = ScriptedProvider([tool_turn([call])] * (MAX_LOOP_STEPS + 2))
    with pytest.raises(AiOutputInvalid) as exc:
        execute_analysis(db_session, repos, case_a["analysis_id"], provider=provider)
    assert "步数" in exc.value.message
    analysis = repos.analyses.get(case_a["analysis_id"])
    assert analysis.status == "FAILED"
    assert analysis.error_code == "AI_OUTPUT_INVALID"
    tool_and_llm = [s for s in _steps(repos, analysis.id) if s.step_type in {"TOOL", "LLM"}]
    assert len(tool_and_llm) <= MAX_LOOP_STEPS


def test_timeout_degrades_to_llm_unavailable(db_session, repos, case_a, tmp_path):
    class FastClock:
        def __init__(self) -> None:
            self.value = 0.0

        def __call__(self) -> float:
            self.value += 1.0
            return self.value

    with pytest.raises(AiUnavailable):
        execute_analysis(
            db_session,
            repos,
            case_a["analysis_id"],
            provider=ReplayProvider(ReplayStore(_empty_dir(tmp_path))),
            timeout_seconds=0.5,
            monotonic=FastClock(),
        )
    analysis = repos.analyses.get(case_a["analysis_id"])
    assert analysis.status == "FAILED"
    assert analysis.error_code == "LLM_UNAVAILABLE"
    assert "超时" in (analysis.error_message or "")


def test_unknown_tool_call_terminates(db_session, repos, case_a):
    provider = ScriptedProvider([tool_turn([ToolCall("create_followup_task", {"title": "x"})])])
    with pytest.raises(AiOutputInvalid):
        execute_analysis(db_session, repos, case_a["analysis_id"], provider=provider)
    analysis = repos.analyses.get(case_a["analysis_id"])
    assert analysis.status == "FAILED"
    assert analysis.error_code == "AI_OUTPUT_INVALID"
    assert any(step.step_type == "TOOL" and step.status == "ERROR" for step in _steps(repos, analysis.id))


def test_invalid_json_triggers_one_repair_then_fails(db_session, repos, case_a):
    provider = ScriptedProvider(
        [
            ProviderResult(final=None, raw_text="抱歉，我无法输出 JSON"),
            ProviderResult(final=None, raw_text="还是不会"),
        ]
    )
    with pytest.raises(AiOutputInvalid):
        execute_analysis(db_session, repos, case_a["analysis_id"], provider=provider)
    assert len(provider.calls) == 2
    assert provider.calls[1]["repair_errors"], "第二次重试必须带上错误信息"


def test_invalid_json_repaired_successfully(db_session, repos, case_a):
    good = fixture_result("t2_analyze_exception_case_a.json")
    provider = ScriptedProvider(
        [ProviderResult(final=None, raw_text="not json"), ProviderResult(final=good.final, raw_text=good.raw_text)]
    )
    result = execute_analysis(db_session, repos, case_a["analysis_id"], provider=provider)
    assert result["status"] == "READY"
    validate_steps = [s for s in _steps(repos, case_a["analysis_id"]) if s.step_type == "VALIDATE"]
    assert [step.status for step in validate_steps] == ["ERROR", "OK"]


def test_prompt_refresh_exposes_tool_observations(db_session, repos, case_a, tmp_path):
    """工具执行后重建 prompt：live 模式的第二回合能看到工具观测与知识片段。"""
    from app.ai.replay import default_t2_plan
    from app.services.knowledge_index import reindex_all

    reindex_all(db_session)
    facts = read_models.exception_facts(repos, case_a["exception_id"])
    good = fixture_result("t2_analyze_exception_case_a.json")
    provider = ScriptedProvider([final_result(good.final)], preset=default_t2_plan(facts))

    result = execute_analysis(db_session, repos, case_a["analysis_id"], provider=provider)
    assert result["status"] == "READY"
    prompt = provider.calls[-1]["prompt"]
    assert "get_tracking_events" in prompt
    assert "chunk_id=" in prompt
    assert provider.calls[-1]["tools_done"] == [
        "get_order",
        "get_tracking_events",
        "get_customer_sla",
        "get_vehicle",
        "get_exception_history",
        "search_knowledge",
        "search_knowledge",
    ]


def test_execute_analysis_unknown_id_raises(db_session, repos):
    from app.core.errors import AppError

    with pytest.raises(AppError):
        execute_analysis(db_session, repos, 999999)


# --- T1 ---------------------------------------------------------------------
def test_parse_message_with_fixture(db_session, repos, case_a, tmp_path):
    input_hash = compute_t1_input_hash(case_a["message"].raw_text, "PENDING")
    _fixture_with_hash(tmp_path, "t1_parse_message_case_a.json", input_hash)

    result = parse_message(
        db_session,
        repos,
        case_a["message_id"],
        provider=ReplayProvider(ReplayStore(tmp_path)),
    )
    assert result["status"] == "PARSED"
    assert result["output"]["exception_type"] == "VEHICLE_BREAKDOWN"
    assert result["output"]["location"] == "济南"
    assert result["model"] == "deepseek-chat"
    assert result["is_replay"] is True

    message = repos.messages.get(case_a["message_id"])
    assert message.parse_status == "PARSED"
    assert message.parser_version == "v1"
    assert message.parse_result_json["meta"]["is_replay"] is True
    assert message.parse_error is None


def test_parse_message_template_fallback(db_session, repos, case_a, tmp_path):
    result = parse_message(
        db_session,
        repos,
        case_a["message_id"],
        provider=ReplayProvider(ReplayStore(_empty_dir(tmp_path))),
    )
    assert result["status"] == "PARSED"
    assert result["model"] == "template"
    assert result["output"]["status"] == "REPAIRING"
    assert result["output"]["estimated_recovery_at"] is not None
    message = repos.messages.get(case_a["message_id"])
    assert message.parse_status == "PARSED"


def test_parse_message_failure_marks_message_failed(db_session, repos, case_a):
    provider = ScriptedProvider(
        [
            ProviderResult(final=None, raw_text="nope"),
            ProviderResult(final=None, raw_text="nope again"),
        ]
    )
    with pytest.raises(AiOutputInvalid):
        parse_message(db_session, repos, case_a["message_id"], provider=provider)
    message = repos.messages.get(case_a["message_id"])
    assert message.parse_status == "FAILED"
    assert message.parse_error


# --- T3 ---------------------------------------------------------------------
def test_draft_notice_template_fallback(db_session, repos, case_a, tmp_path):
    notice = draft_notice(
        db_session,
        repos,
        case_a["exception_id"],
        provider=ReplayProvider(ReplayStore(_empty_dir(tmp_path))),
    )
    assert set(notice) == {"subject", "content", "tone"}
    assert notice["tone"] == "APOLOGETIC"
    assert "SO20260930021" in notice["content"]
    assert "2026-10-01 13:30" in notice["content"]
    assert "270 分钟" in notice["content"]
    assert len(notice["subject"]) <= 60 and len(notice["content"]) <= 500


def test_draft_notice_with_fixture(db_session, repos, case_a, tmp_path):
    facts = read_models.exception_facts(repos, case_a["exception_id"])
    input_hash = compute_t3_input_hash(None, facts)
    _fixture_with_hash(tmp_path, "t3_draft_notice_case_a.json", input_hash)
    notice = draft_notice(
        db_session,
        repos,
        case_a["exception_id"],
        provider=ReplayProvider(ReplayStore(tmp_path)),
    )
    assert notice["subject"].startswith("【延误通知】")


def test_draft_notice_fact_violation_raises(db_session, repos, case_a):
    output = fixture_result("t3_draft_notice_case_a.json").final
    output["content"] = "订单 SO20260930021 预计 2026-11-11 08:00 到达，延误 999 分钟。"
    provider = ScriptedProvider([final_result(output), final_result(output)])
    with pytest.raises(AiOutputInvalid) as exc:
        draft_notice(db_session, repos, case_a["exception_id"], provider=provider)
    assert "事实之外的时间" in exc.value.message or "延误口径" in exc.value.message


def test_draft_notice_unknown_exception_raises(db_session, repos):
    from app.core.errors import AppError

    with pytest.raises(AppError):
        draft_notice(db_session, repos, 999999)


def test_input_hash_is_deterministic(repos, case_a):
    facts = read_models.exception_facts(repos, case_a["exception_id"])
    assert compute_t2_input_hash(facts) == compute_t2_input_hash(facts)
    assert len(compute_t2_input_hash(facts)) == 64
    assert compute_t1_input_hash("a", "PENDING") != compute_t1_input_hash("a", "PARSED")
    assert compute_t3_input_hash(None, facts) == compute_t3_input_hash(None, facts)
    assert F.backend_risk_level(facts) == "HIGH"
