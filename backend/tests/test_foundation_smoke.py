"""地基冒烟测试（Lead 维护）：时钟/规则/模型/脚手架是否可用。

不属于任何智能体的写作用域，改动需经 Lead。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.core.clock import parse_dt
from app.models.enums import ExceptionLevel, ExceptionStatus, OrderStatus
from app.rules import detection, eta, risk, sla, state_machine


def test_healthz(client):
    response = client.get("/api/v1/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["model_tables"] == 22
    assert body["clock_mode"] == "replay"


def test_clock_is_deterministic():
    first = parse_dt("2026-09-30T09:00:00+08:00")
    second = parse_dt("2026-09-30T09:00:00+08:00")
    assert first == second
    assert first.tzinfo == UTC
    assert first.hour == 1  # 09:00 +08:00 == 01:00 UTC


def test_bootstrap_fixture_has_four_roles(bootstrap):
    assert set(bootstrap["headers"]) == {"OWNER", "ADMIN", "OPERATOR", "VIEWER"}
    assert bootstrap["customers"]["vip"].level == "VIP"
    assert len(bootstrap["sla_rules"]) == 3


def test_state_machine_rejects_invalid_transition():
    state_machine.plan_transition("ORDER", OrderStatus.CREATED, OrderStatus.DISPATCHED)
    try:
        state_machine.plan_transition("ORDER", OrderStatus.CREATED, OrderStatus.DELIVERED)
    except Exception as exc:  # AppError
        assert "不允许的状态流转" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("非法流转必须报错")

    state_machine.plan_transition("EXCEPTION", ExceptionStatus.DETECTED, ExceptionStatus.PROCESSING)
    assert state_machine.is_terminal("EXCEPTION", ExceptionStatus.CLOSED)


def test_sla_rule_matching_prefers_most_specific(bootstrap):
    rules = bootstrap["sla_rules"]
    match = sla.match_rule(rules, customer_code="VIP-01", customer_level="VIP")
    assert match.deadline_offset_hours == 24
    assert match.scope_type == "CUSTOMER"

    match_normal = sla.match_rule(rules, customer_code="NORM-01", customer_level="NORMAL")
    assert match_normal.deadline_offset_hours == 30
    assert match_normal.max_delay_minutes == 30


def test_sla_breach_boundary(bootstrap):
    rules = bootstrap["sla_rules"]
    match = sla.match_rule(rules, customer_code="NORM-01", customer_level="NORMAL")
    promised = datetime(2026, 9, 30, 10, 0)
    ok = sla.evaluate(match, promised_delivery_at=promised, expected_eta_at=promised + timedelta(minutes=30))
    assert ok.breached is False and ok.delay_minutes == 30
    bad = sla.evaluate(match, promised_delivery_at=promised, expected_eta_at=promised + timedelta(minutes=31))
    assert bad.breached is True and bad.delay_minutes == 31


def test_risk_case_a_is_critical():
    result = risk.evaluate_risk(
        delay_minutes=270, customer_level="VIP", exception_type="VEHICLE_BREAKDOWN", sla_breached=True
    )
    assert result.score == 4
    assert result.level == ExceptionLevel.CRITICAL
    codes = {factor["code"] for factor in result.factor_dicts}
    assert {"DELAY_BASE", "CUSTOMER_VIP", "VEHICLE_BREAKDOWN", "SLA_BREACH"} <= codes


def test_risk_case_d_is_medium_without_breach():
    result = risk.evaluate_risk(
        delay_minutes=25, customer_level="NORMAL", exception_type="DELAY_RISK", sla_breached=False
    )
    assert result.score == 1
    assert result.level == ExceptionLevel.MEDIUM


def test_detection_stall_rule():
    now = datetime(2026, 9, 30, 11, 0)
    decision = detection.decide(
        now=now,
        order_status=OrderStatus.IN_TRANSIT,
        last_move_at=now - timedelta(minutes=130),
        stall_threshold_minutes=120,
    )
    assert decision.should_create is True
    assert decision.rule == "STALL_OVER_THRESHOLD"
    assert decision.exception_type == "VEHICLE_BREAKDOWN"


def test_detection_eta_breach_rule():
    now = datetime(2026, 9, 30, 11, 0)
    decision = detection.decide(
        now=now,
        order_status=OrderStatus.IN_TRANSIT,
        last_move_at=now - timedelta(minutes=10),
        stall_threshold_minutes=120,
        expected_eta_at=now + timedelta(hours=5),
        promised_delivery_at=now + timedelta(hours=1),
        max_delay_minutes=30,
    )
    assert decision.rule == "ETA_BREACH_SLA"
    assert decision.exception_type == "DELAY_RISK"


def test_eta_repair_wait_method():
    now = datetime(2026, 9, 30, 11, 0)
    result = eta.recalc(
        now=now,
        distance_km=400,
        progress_ratio=0.5,
        repair_recovery_at=now + timedelta(hours=1),
    )
    assert result.method == eta.EtaMethod.REPAIR_WAIT
    assert result.remaining_km == 200
    assert result.eta_at > now
