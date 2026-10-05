"""Replay 三级降级：fixture 精确命中 → 模板回退；以及 fixture 目录解析。"""

from __future__ import annotations

import json

import pytest

from app.ai import facts as F
from app.ai.guard import EvidenceIndex, validate_t1, validate_t2, validate_t3
from app.ai.replay import (
    ReplayProvider,
    ReplayStore,
    default_t2_plan,
    knowledge_query,
    plan_from_steps,
    resolve_replay_dir,
)
from app.ai.templates import build_t1_output, build_t2_output, build_t3_output
from app.ai.tools import ToolContext, ToolOutcome, call_tool
from app.services import read_models
from tests.ai.fake_llm import FIXTURE_DIR, load_fixture


def _empty_dir(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    return empty


def _tool_results(repos, case_a) -> dict[str, ToolOutcome]:
    ctx = ToolContext(repos=repos, exception_id=case_a["exception_id"])
    return {
        "get_order": call_tool("get_order", {"order_id": case_a["order_id"]}, ctx),
        "get_tracking_events": call_tool("get_tracking_events", {"order_id": case_a["order_id"]}, ctx),
        "get_vehicle": call_tool("get_vehicle", {"vehicle_id": case_a["case"].vehicle_id}, ctx),
    }


def test_shipped_fixtures_are_loadable_and_counted():
    store = ReplayStore(FIXTURE_DIR)
    counts = store.counts()
    assert counts == {"ANALYZE_EXCEPTION": 2, "PARSE_MESSAGE": 1, "DRAFT_NOTICE": 1}
    assert store.errors == []
    for fixture in store.fixtures():
        assert fixture.output is not None
        assert fixture.model


def test_replay_dir_resolution_points_to_backend_fixtures():
    resolved = resolve_replay_dir("tests/fixtures/ai")
    assert resolved.is_absolute() and resolved.exists()
    assert (resolved / "t2_analyze_exception_case_a.json").exists()


def test_exact_hash_match_and_match_any(tmp_path):
    payload = load_fixture("t2_analyze_exception_case_a.json")
    payload["input_hash"] = "abc123"
    (tmp_path / "exact.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    wildcard = dict(payload)
    wildcard["input_hash"] = None
    wildcard["match_any"] = True
    (tmp_path / "wild.json").write_text(json.dumps(wildcard, ensure_ascii=False), encoding="utf-8")

    store = ReplayStore(tmp_path)
    assert store.find("ANALYZE_EXCEPTION", "abc123").input_hash == "abc123"
    assert store.find("ANALYZE_EXCEPTION", "other").match_any is True
    assert store.find("PARSE_MESSAGE", "other") is None


def test_no_fixture_returns_none_and_broken_fixture_is_tolerated(tmp_path):
    (tmp_path / "broken.json").write_text("{ not json", encoding="utf-8")
    store = ReplayStore(tmp_path)
    assert store.find("ANALYZE_EXCEPTION", "anything") is None
    assert store.errors and "broken.json" in store.errors[0]


def test_plan_from_steps_ignores_non_tool_steps():
    steps = [
        {"step_type": "TOOL", "tool_name": "get_order", "args": {"order_id": 1}},
        {"step_type": "LLM", "result_summary": "x"},
        {"step_type": "TOOL", "tool_name": "get_vehicle", "input": {"vehicle_id": 2}},
    ]
    calls = plan_from_steps(steps)
    assert [(call.name, call.args) for call in calls] == [
        ("get_order", {"order_id": 1}),
        ("get_vehicle", {"vehicle_id": 2}),
    ]


def test_default_plan_matches_documented_tool_order(repos, case_a):
    facts = read_models.exception_facts(repos, case_a["exception_id"])
    names = [call.name for call in default_t2_plan(facts)]
    assert names == [
        "get_order",
        "get_tracking_events",
        "get_customer_sla",
        "get_vehicle",
        "get_exception_history",
        "search_knowledge",
    ]
    # 检索词跟着**建单原因**走：CASE-A 在 AI 层夹具里是延误单（车辆单不做 SLA 判定，
    # 需要"已违约"的夹具只能按延误口径造）→ 默认计划检索"延误"
    assert knowledge_query(facts) == "延误"


def test_template_t2_is_guard_valid_without_fixture(repos, case_a):
    facts = read_models.exception_facts(repos, case_a["exception_id"])
    outcomes = _tool_results(repos, case_a)
    output = build_t2_output(facts=facts, tool_results=outcomes)
    index = EvidenceIndex()
    index.add_facts(facts)
    for outcome in outcomes.values():
        index.add_tool_outcome(outcome)
    validated = validate_t2(output, facts=facts, evidence=index)
    assert validated["impact"]["delay_minutes"] == case_a["delay_minutes"]
    kinds = {ref["type"] for ref in validated["evidence_refs"]}
    assert "KNOWLEDGE_CHUNK" not in kinds  # 未调用 search_knowledge → 不得引用知识片段
    assert kinds & {"ORDER", "TRACKING_EVENT", "VEHICLE"}


def test_template_t1_parses_relative_evening(repos, case_a, base_time):
    facts = read_models.exception_facts(repos, case_a["exception_id"])
    output = build_t1_output(
        raw_text="车在济南爆胎了，正在修，师傅说晚上8点能好",
        now=base_time,
        occurred_at=F.case_of(facts)["occurred_at"],
    )
    assert output["estimated_recovery_at"] == "2026-09-30T12:00:00Z"  # 20:00 +08:00
    assert output["status"] == "REPAIRING"
    validate_t1(output, now=base_time, occurred_at=F.case_of(facts)["occurred_at"])


def test_template_status_for_reported_sentence_matches_backend_fallback():
    """真机联调回归：含维修信息必须判为 REPAIRING，不能掉成 UNKNOWN。"""
    output = build_t1_output(
        raw_text="车在济南爆胎了，现在联系修理厂，预计晚上 8 点恢复。",
        now="2026-09-30T01:00:00Z",
        occurred_at="2026-09-30T01:00:00Z",
    )
    assert output["status"] == "REPAIRING"
    assert output["estimated_recovery_at"] == "2026-09-30T12:00:00Z"  # 20:00 +08:00
    assert output["exception_type"] == "VEHICLE_BREAKDOWN"
    assert output["confidence"] >= 0.6


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("车在济南爆胎了，现在联系修理厂，预计晚上 8 点恢复。", "REPAIRING"),
        ("车辆爆胎，已联系维修点处理", "REPAIRING"),
        ("师傅说还要一会儿", "UNKNOWN"),
        ("轮胎要换，等配件，明天上午到", "WAITING_PARTS"),
        ("在济南爆胎了，修理厂说还在等件", "WAITING_PARTS"),
        ("车抛锚了，走不了", "BREAKDOWN"),
        ("车辆熄火无法行驶", "BREAKDOWN"),
        ("已恢复行驶，继续出发", "MOVING"),
    ],
)
def test_template_status_keywords_align_with_backend_domain(text, expected):
    output = build_t1_output(
        raw_text=text,
        now="2026-09-30T01:00:00Z",
        occurred_at="2026-09-30T01:00:00Z",
    )
    assert output["status"] == expected


def test_template_t3_formal_when_not_breached(repos, case_a):
    facts = read_models.exception_facts(repos, case_a["exception_id"])
    facts["exception"]["sla_breached"] = False
    facts["exception"]["sla_delay_minutes"] = 0
    output = build_t3_output(facts=facts, analysis={})
    assert output["tone"] == "FORMAL"
    validate_t3(output, facts=facts)


def test_replay_provider_prefers_fixture_and_reports_template(tmp_path, repos, case_a):
    provider = ReplayProvider(ReplayStore(_empty_dir(tmp_path)))
    facts = read_models.exception_facts(repos, case_a["exception_id"])
    state = type(
        "S",
        (),
        {
            "task_type": "ANALYZE_EXCEPTION",
            "facts": facts,
            "input_hash": "nope",
            "tool_results": _tool_results(repos, case_a),
            "context": {},
        },
    )()
    plan = provider.preset_plan("ANALYZE_EXCEPTION", state=state)
    assert [call.name for call in plan] == [  # 无 fixture → 默认计划
        "get_order",
        "get_tracking_events",
        "get_customer_sla",
        "get_vehicle",
        "get_exception_history",
        "search_knowledge",
    ]
    result = provider.next_step("ANALYZE_EXCEPTION", state=state, repair_errors=[])
    assert result.model == "template"
    assert result.is_replay is True
    assert result.final["impact"]["delay_minutes"] == case_a["delay_minutes"]


@pytest.mark.parametrize("name", ["t1_parse_message_case_a.json", "t2_analyze_exception_case_a.json"])
def test_shipped_fixtures_have_no_hash_by_design(name):
    """录制样本默认不做通配匹配：真实 DB 的 id 变化时自动走模板回退（设计目标）。"""
    payload = load_fixture(name)
    assert payload["input_hash"] is None
    assert payload["match_any"] is False
