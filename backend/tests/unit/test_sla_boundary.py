"""SLA 规则匹配与违约边界（§8.3）：具体客户 > 客户等级 > 默认；边界用 min/min+1 卡死。"""

from __future__ import annotations

from datetime import timedelta

from app.models.enums import SlaScopeType
from app.rules import sla


def test_rule_matching_priority(bootstrap):
    rules = bootstrap["sla_rules"]
    vip = sla.match_rule(rules, customer_code="VIP-01", customer_level="VIP")
    assert vip.scope_type == str(SlaScopeType.CUSTOMER)
    assert vip.deadline_offset_hours == 24
    assert vip.max_delay_minutes == 0

    level_only = sla.match_rule(rules, customer_code="VIP-02", customer_level="VIP")
    assert level_only.scope_type == str(SlaScopeType.CUSTOMER_LEVEL)
    assert level_only.deadline_offset_hours == 24

    normal = sla.match_rule(rules, customer_code="NORM-01", customer_level="NORMAL")
    assert normal.scope_type == str(SlaScopeType.DEFAULT)
    assert normal.deadline_offset_hours == 30
    assert normal.max_delay_minutes == 30


def test_matching_falls_back_to_builtin_default():
    match = sla.match_rule([], customer_code="X", customer_level="VIP")
    assert match.is_default is True
    assert match.deadline_offset_hours == sla.DEFAULT_OFFSET_HOURS
    assert match.max_delay_minutes == sla.DEFAULT_MAX_DELAY_MINUTES


def test_same_scope_prefers_lowest_priority_then_id(bootstrap):
    extra = [
        type(
            "Rule",
            (),
            {
                "id": 900,
                "name": "低优先",
                "scope_type": "DEFAULT",
                "scope_value": None,
                "deadline_offset_hours": 10,
                "max_delay_minutes": 10,
                "priority": 90,
                "is_active": True,
            },
        )()
    ]
    match = sla.match_rule(list(bootstrap["sla_rules"]) + extra, customer_code="NORM-01", customer_level="NORMAL")
    assert match.deadline_offset_hours == 10
    assert match.rule_id == 900


def test_breach_boundary_is_strict_greater_than(bootstrap):
    match = sla.match_rule(bootstrap["sla_rules"], customer_code="NORM-01", customer_level="NORMAL")
    promised = bootstrap["base_time"].replace(tzinfo=None)

    on_boundary = sla.evaluate(
        match, promised_delivery_at=promised, expected_eta_at=promised + timedelta(minutes=30)
    )
    assert on_boundary.delay_minutes == 30
    assert on_boundary.breached is False

    over_boundary = sla.evaluate(
        match, promised_delivery_at=promised, expected_eta_at=promised + timedelta(minutes=31)
    )
    assert over_boundary.delay_minutes == 31
    assert over_boundary.breached is True


def test_vip_zero_tolerance_breaches_immediately(bootstrap):
    match = sla.match_rule(bootstrap["sla_rules"], customer_code="VIP-01", customer_level="VIP")
    promised = bootstrap["base_time"].replace(tzinfo=None)
    exact = sla.evaluate(match, promised_delivery_at=promised, expected_eta_at=promised)
    assert exact.breached is False and exact.delay_minutes == 0
    late = sla.evaluate(match, promised_delivery_at=promised, expected_eta_at=promised + timedelta(minutes=1))
    assert late.breached is True and late.delay_minutes == 1


def test_missing_eta_yields_zero_delay():
    match = sla.match_rule([], customer_code=None, customer_level=None)
    impact = sla.evaluate(match, promised_delivery_at=None, expected_eta_at=None)
    assert impact.delay_minutes == 0
    assert impact.breached is False


def test_compute_promised_at_from_dispatched(db_session, bootstrap):
    match = sla.match_rule(bootstrap["sla_rules"], customer_code="VIP-01", customer_level="VIP")
    dispatched = bootstrap["base_time"]
    promised = sla.compute_promised_at(dispatched, match.deadline_offset_hours)
    assert promised == bootstrap["promised_vip"].replace(tzinfo=None)
    assert sla.compute_promised_at(None, 24) is None
