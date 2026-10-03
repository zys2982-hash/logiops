"""输出长度降级契约（Lead 维护）。

背景（真实模型实测）：模型很容易把 summary/rationale 写超（上限 300 字却写 500 字），
直接判 schema 失败会导致"重试仍然超长 → 重新分析没用"。现在改为**按 schema 上限无损截断**：
只压缩表述，不改事实；事实类错误（延误口径、编造来源）仍然照旧拦下。
"""

from __future__ import annotations

import pytest

from app.ai.guard import GuardError, validate_t1, validate_t2


def _facts() -> dict:
    return {
        "now": "2026-09-30T01:00:00Z",
        "exception": {
            "id": 1,
            "case_no": "EX20260930001",
            "type": "VEHICLE_BREAKDOWN",
            "level": "CRITICAL",
            "sla_delay_minutes": 270,
            "sla_breached": True,
            "expected_eta_at": "2026-09-30T22:00:00Z",
            "promised_delivery_at": "2026-09-30T17:30:00Z",
        },
        "order": {"id": 10, "order_no": "SO20260930021"},
        "customer": {"id": 20, "name": "远洋集团", "level": "VIP"},
        "vehicle": {"id": 30, "plate_no": "津A·12345", "status": "REPAIRING"},
    }


def _t2_payload(summary_length: int) -> dict:
    return {
        "summary": "摘" * summary_length,
        "root_cause": {"code": "VEHICLE_BREAKDOWN", "note": "承运商反馈济南爆胎"},
        "impact": {"delay_minutes": 270, "sla_breached": True, "affected_customer_level": "VIP"},
        "suggestions": [{"code": "UPDATE_ETA", "title": "更新 ETA", "rationale": "按恢复时间重算"}],
        "open_questions": [],
        "evidence_refs": [{"type": "ORDER", "id": 10, "note": "订单"}],
    }


def test_overlong_summary_is_trimmed_not_rejected():
    result = validate_t2(_t2_payload(500), facts=_facts())
    assert len(result["summary"]) <= 300, "超长 summary 必须被截断到 schema 上限"
    assert result["summary"].endswith("…")
    assert result.get("_guard_notes"), "截断要留下可追溯的说明"
    assert any("summary" in note for note in result["_guard_notes"])
    # 事实字段不受影响
    assert result["impact"]["delay_minutes"] == 270
    assert result["impact"]["sla_breached"] is True


def test_overlong_suggestion_field_is_trimmed():
    payload = _t2_payload(100)
    payload["suggestions"][0]["title"] = "标题" * 100  # 超过 120 字
    payload["suggestions"][0]["rationale"] = "理由" * 200  # 超过 300 字
    result = validate_t2(payload, facts=_facts())
    assert len(result["suggestions"][0]["title"]) <= 120
    assert len(result["suggestions"][0]["rationale"]) <= 300
    assert result.get("_guard_notes")


def test_too_many_suggestions_are_truncated():
    payload = _t2_payload(80)
    payload["suggestions"] = [
        {"code": "UPDATE_ETA", "title": f"建议{i}", "rationale": "r"} for i in range(9)
    ]
    result = validate_t2(payload, facts=_facts())
    assert len(result["suggestions"]) == 5, "超过 5 条建议按上限裁剪"
    assert result.get("_guard_notes")


def test_normal_length_payload_has_no_notes():
    result = validate_t2(_t2_payload(120), facts=_facts())
    assert "_guard_notes" not in result
    assert len(result["summary"]) == 120


def test_fact_mismatch_is_still_rejected():
    """截断只处理"话多"，事实类错误（延误口径不一致）照旧拦截。"""
    payload = _t2_payload(100)
    payload["impact"]["delay_minutes"] = 999
    with pytest.raises(GuardError) as exc:
        validate_t2(payload, facts=_facts())
    assert any("delay_minutes" in item for item in exc.value.errors)


def test_fabricated_evidence_is_still_rejected():
    payload = _t2_payload(100)
    payload["evidence_refs"] = [{"type": "TRACKING_EVENT", "id": 999999, "note": "编造"}]
    with pytest.raises(GuardError):
        validate_t2(payload, facts=_facts())


def test_t1_overlong_location_is_trimmed():
    payload = {
        "exception_type": "VEHICLE_BREAKDOWN",
        "location": "济南" * 60,  # 超过 64 字
        "status": "REPAIRING",
        "estimated_recovery_at": None,
        "confidence": 0.8,
        "missing_info": [],
    }
    result = validate_t1(payload, now="2026-09-30T01:00:00Z", occurred_at="2026-09-29T22:15:00Z")
    assert len(result["location"]) <= 64
    assert result.get("_guard_notes")
