"""Guard：Schema 约束 + 事实一致性（防幻觉核心）。"""

from __future__ import annotations

import pytest

from app.ai import facts as F
from app.ai.guard import EvidenceIndex, GuardError, validate_t1, validate_t2, validate_t3
from app.ai.templates import build_t1_output, build_t2_output, build_t3_output
from app.ai.tools import ToolContext, call_tool
from app.services import read_models


def _facts(repos, case_a) -> dict:
    return read_models.exception_facts(repos, case_a["exception_id"])


def _tool_results(repos, case_a) -> dict:
    ctx = ToolContext(repos=repos, exception_id=case_a["exception_id"])
    return {
        "get_order": call_tool("get_order", {"order_id": case_a["order_id"]}, ctx),
        "get_tracking_events": call_tool("get_tracking_events", {"order_id": case_a["order_id"], "limit": 10}, ctx),
        "get_vehicle": call_tool("get_vehicle", {"vehicle_id": case_a["case"].vehicle_id}, ctx),
    }


def _evidence(facts: dict, outcomes: dict) -> EvidenceIndex:
    index = EvidenceIndex()
    index.add_facts(facts)
    for outcome in outcomes.values():
        index.add_tool_outcome(outcome)
    return index


def _valid_t2(repos, case_a) -> tuple[dict, dict, EvidenceIndex]:
    facts = _facts(repos, case_a)
    outcomes = _tool_results(repos, case_a)
    output = build_t2_output(facts=facts, tool_results=outcomes)
    return output, facts, _evidence(facts, outcomes)


def test_valid_template_output_passes(repos, case_a):
    output, facts, evidence = _valid_t2(repos, case_a)
    result = validate_t2(output, facts=facts, evidence=evidence)
    assert result["impact"]["delay_minutes"] == case_a["delay_minutes"]
    assert result["impact"]["sla_breached"] is True
    assert len(result["suggestions"]) >= 1


def test_fabricated_delay_minutes_is_blocked(repos, case_a):
    output, facts, evidence = _valid_t2(repos, case_a)
    output["impact"]["delay_minutes"] = 999
    with pytest.raises(GuardError) as exc:
        validate_t2(output, facts=facts, evidence=evidence)
    assert "sla_delay_minutes" in str(exc.value)


def test_delay_tolerance_is_five_minutes(repos, case_a):
    output, facts, evidence = _valid_t2(repos, case_a)
    output["impact"]["delay_minutes"] = case_a["delay_minutes"] + 5
    validate_t2(output, facts=facts, evidence=evidence)
    output["impact"]["delay_minutes"] = case_a["delay_minutes"] + 6
    with pytest.raises(GuardError):
        validate_t2(output, facts=facts, evidence=evidence)


def test_fabricated_sla_breach_is_blocked(repos, case_a):
    output, facts, evidence = _valid_t2(repos, case_a)
    output["impact"]["sla_breached"] = False
    with pytest.raises(GuardError) as exc:
        validate_t2(output, facts=facts, evidence=evidence)
    assert "sla_breached" in str(exc.value)


def test_suggestion_code_must_be_in_approval_whitelist(repos, case_a):
    output, facts, evidence = _valid_t2(repos, case_a)
    output["suggestions"][0]["code"] = "DELETE_ORDER"
    with pytest.raises(GuardError) as exc:
        validate_t2(output, facts=facts, evidence=evidence)
    assert "suggestions" in str(exc.value)


def test_fabricated_evidence_id_is_blocked(repos, case_a):
    output, facts, evidence = _valid_t2(repos, case_a)
    output["evidence_refs"].append({"type": "TRACKING_EVENT", "id": 999999, "note": "编造的轨迹"})
    with pytest.raises(GuardError) as exc:
        validate_t2(output, facts=facts, evidence=evidence)
    assert "防编造来源" in str(exc.value)


def test_evidence_type_must_be_known(repos, case_a):
    output, facts, evidence = _valid_t2(repos, case_a)
    output["evidence_refs"].append({"type": "NEWS_ARTICLE", "id": 1, "note": "x"})
    with pytest.raises(GuardError):
        validate_t2(output, facts=facts, evidence=evidence)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda out: out.update({"summary": "长" * 301}),
        lambda out: out.update({"suggestions": []}),
        lambda out: out.update({"open_questions": ["q"] * 6}),
        lambda out: out.update({"root_cause": {"code": "ALIEN", "note": "x"}}),
        lambda out: out.update({"extra_field": 1}),
        lambda out: out.update({"root_cause": {"code": "TRAFFIC", "note": ""}}),
    ],
)
def test_schema_constraints(repos, case_a, mutate):
    output, facts, evidence = _valid_t2(repos, case_a)
    mutate(output)
    with pytest.raises(GuardError):
        validate_t2(output, facts=facts, evidence=evidence)


def test_t1_template_output_passes(repos, case_a, base_time):
    facts = _facts(repos, case_a)
    output = build_t1_output(
        raw_text="车在济南爆胎了，正在修，师傅说晚上8点能好",
        now=base_time,
        occurred_at=F.case_of(facts)["occurred_at"],
    )
    result = validate_t1(output, now=base_time, occurred_at=F.case_of(facts)["occurred_at"])
    assert result["exception_type"] == "VEHICLE_BREAKDOWN"
    assert result["location"] == "济南"
    assert result["confidence"] >= 0.6


def test_t1_recovery_window_enforced(repos, case_a, base_time):
    facts = _facts(repos, case_a)
    occurred = F.case_of(facts)["occurred_at"]
    output = build_t1_output(raw_text="车在济南爆胎了", now=base_time, occurred_at=occurred)
    output["estimated_recovery_at"] = "2026-10-15T12:00:00Z"  # 远超 48h
    with pytest.raises(GuardError) as exc:
        validate_t1(output, now=base_time, occurred_at=occurred)
    assert "48" in str(exc.value)


def test_t1_schema_rejects_unknown_enum(repos, case_a, base_time):
    with pytest.raises(GuardError):
        validate_t1(
            {
                "exception_type": "ALIEN_ATTACK",
                "location": "济南",
                "status": "REPAIRING",
                "estimated_recovery_at": None,
                "confidence": 0.5,
                "missing_info": [],
            },
            now=base_time,
            occurred_at=base_time,
        )


def test_t3_template_output_passes(repos, case_a):
    facts = _facts(repos, case_a)
    output = build_t3_output(facts=facts, analysis={})
    result = validate_t3(output, facts=facts)
    assert result["tone"] == "APOLOGETIC"
    assert "SO20260930021" in result["content"]


@pytest.mark.parametrize(
    ("field", "value", "fragment"),
    [
        ("content", "尊敬的客户：您的货物已在路上，请耐心等待。", "订单号"),
        ("content", "订单 SO20260930021 预计 2026-11-11 08:00 到达，延误 270 分钟。", "事实之外的时间"),
        ("content", "订单 SO20260930021 预计 2026-10-01 13:30 到达，延误 90 分钟。", "延误口径"),
        ("content", "订单 SO99999999999 预计 2026-10-01 13:30 到达，延误 270 分钟。", "未授权的订单号"),
        ("subject", "长" * 61, "subject"),
    ],
)
def test_t3_fact_checks_block_hallucination(repos, case_a, field, value, fragment):
    facts = _facts(repos, case_a)
    output = build_t3_output(facts=facts, analysis={})
    output[field] = value
    with pytest.raises(GuardError) as exc:
        validate_t3(output, facts=facts)
    assert fragment in str(exc.value)


def test_evidence_index_from_facts_only(repos, case_a):
    facts = _facts(repos, case_a)
    index = EvidenceIndex()
    index.add_facts(facts)
    assert index.contains("ORDER", case_a["order_id"])
    assert index.contains("EXCEPTION", case_a["exception_id"])
    assert index.contains("CARRIER_MESSAGE", case_a["message_id"])
    assert index.contains("TRACKING_EVENT", case_a["event_ids"][0])
    assert not index.contains("KNOWLEDGE_CHUNK", 7)  # 必须来自本次工具返回
