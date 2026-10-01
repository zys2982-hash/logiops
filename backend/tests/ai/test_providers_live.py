"""Live 模式的解析路径（用桩客户端，不联网）：tool-call 回合与最终 JSON 回合。"""

from __future__ import annotations

import json

from app.ai.agent import AgentState
from app.ai.llm import LLMResponse
from app.ai.providers import (
    LiveProvider,
    observation_text,
    parse_json_object,
    tool_calls_from_payload,
)
from app.ai.tools import ToolOutcome


class StubClient:
    def __init__(self, texts: list[str]) -> None:
        self._texts = list(texts)
        self.users: list[str] = []

    def complete(self, *, task_type, system, user, temperature=None, max_output_tokens=None, tools=None):
        self.users.append(user)
        return LLMResponse(
            text=self._texts.pop(0),
            model="stub-model",
            tokens_in=11,
            tokens_out=7,
            latency_ms=5,
        )


def test_parse_json_object_handles_fences_and_noise():
    assert parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_object('好的：{"a": {"b": 2}} 以上。') == {"a": {"b": 2}}
    assert parse_json_object("抱歉我无法回答") is None
    assert parse_json_object(None) is None


def test_tool_calls_from_payload_variants():
    multi = tool_calls_from_payload({"tool_calls": [{"name": "get_order", "args": {"order_id": 3}}]})
    assert multi[0].name == "get_order"
    single = tool_calls_from_payload({"tool_call": {"name": "get_vehicle", "arguments": '{"vehicle_id": 5}'}})
    assert single[0].args == {"vehicle_id": 5}
    assert tool_calls_from_payload({"summary": "no tools"}) == []


def test_observation_text_summarises_tool_results():
    state = AgentState(task_type="ANALYZE_EXCEPTION")
    assert observation_text(state) == "（尚未调用工具）"
    state.tool_results["get_order"] = ToolOutcome(
        name="get_order", args={"order_id": 1}, payload={"order_no": "SO1"}, summary="SO1 天津→上海"
    )
    text = observation_text(state)
    assert "get_order" in text and "SO1 天津→上海" in text


def test_live_provider_returns_tool_calls_then_final():
    tool_turn = json.dumps({"tool_calls": [{"name": "get_order", "args": {"order_id": 1}}]})
    final_turn = json.dumps({"summary": "ok"})
    client = StubClient([tool_turn, final_turn])
    provider = LiveProvider(client=client)
    state = AgentState(task_type="ANALYZE_EXCEPTION", system="sys", prompt="prompt")

    assert provider.preset_plan("ANALYZE_EXCEPTION", state=state) is None
    first = provider.next_step("ANALYZE_EXCEPTION", state=state, repair_errors=[])
    assert first.tool_calls and first.tool_calls[0].name == "get_order"
    assert first.is_replay is False and first.model == "stub-model"

    state.tool_results["get_order"] = ToolOutcome("get_order", {}, {"id": 1}, "ok")
    second = provider.next_step("ANALYZE_EXCEPTION", state=state, repair_errors=["延迟不一致"])
    assert second.final == {"summary": "ok"}
    assert "延迟不一致" in client.users[-1]
    assert "已执行的只读工具结果" in client.users[-1]


def test_live_provider_returns_final_none_for_unparsable_text():
    client = StubClient(["我想说明一下，这个情况比较复杂"])
    provider = LiveProvider(client=client)
    state = AgentState(task_type="ANALYZE_EXCEPTION", system="sys", prompt="prompt")
    result = provider.next_step("ANALYZE_EXCEPTION", state=state, repair_errors=[])
    assert result.final is None
    assert result.raw_text
