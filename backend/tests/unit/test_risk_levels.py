"""风险等级四档（§8.6）：基础分 + VIP/SVIP + 车辆故障 + SLA 违约，封顶 4。"""

from __future__ import annotations

from app.models.enums import ExceptionLevel
from app.rules import risk


def test_level_low_without_delay_or_additions():
    result = risk.evaluate_risk(delay_minutes=0, customer_level="NORMAL", exception_type="DELAY_RISK")
    assert result.score == 0
    assert result.level == str(ExceptionLevel.LOW)


def test_medium_when_delay_under_120():
    result = risk.evaluate_risk(delay_minutes=120, customer_level="NORMAL", exception_type="DELAY_RISK")
    assert risk.base_score(120) == 1
    assert result.score == 1
    assert result.level == str(ExceptionLevel.MEDIUM)


def test_case_d_boundary_25_minutes_is_medium():
    result = risk.evaluate_risk(
        delay_minutes=25, customer_level="NORMAL", exception_type="DELAY_RISK", sla_breached=False
    )
    assert result.score == 1
    assert result.level == str(ExceptionLevel.MEDIUM)


def test_high_when_score_three():
    result = risk.evaluate_risk(
        delay_minutes=200, customer_level="VIP", exception_type="DELAY_RISK", sla_breached=False
    )
    assert result.score == 3
    assert result.level == str(ExceptionLevel.HIGH)


def test_case_a_is_critical_capped_at_four():
    result = risk.evaluate_risk(
        delay_minutes=270,
        customer_level="VIP",
        exception_type="VEHICLE_BREAKDOWN",
        sla_breached=True,
    )
    assert result.score == 4
    assert result.level == str(ExceptionLevel.CRITICAL)
    codes = {factor["code"] for factor in result.factor_dicts}
    assert codes == {"DELAY_BASE", "CUSTOMER_VIP", "VEHICLE_BREAKDOWN", "SLA_BREACH"}
    assert all(factor["weight"] >= 0 for factor in result.factor_dicts)


def test_svip_adds_two_points():
    result = risk.evaluate_risk(
        delay_minutes=0, customer_level="SVIP", exception_type="DELAY_RISK", sla_breached=False
    )
    assert result.score == 2
    assert result.level == str(ExceptionLevel.MEDIUM)
    assert {factor["code"] for factor in result.factor_dicts} == {"DELAY_BASE", "CUSTOMER_SVIP"}


def test_base_score_buckets():
    assert risk.base_score(None) == 0
    assert risk.base_score(-5) == 0
    assert risk.base_score(1) == 1
    assert risk.base_score(120) == 1
    assert risk.base_score(121) == 2
    assert risk.base_score(360) == 2
    assert risk.base_score(361) == 3


def test_vip_upgrade_can_be_disabled():
    result = risk.evaluate_risk(
        delay_minutes=1,
        customer_level="VIP",
        exception_type="VEHICLE_BREAKDOWN",
        sla_breached=False,
        vip_upgrade=False,
    )
    assert result.score == 2
    assert "CUSTOMER_VIP" not in {factor["code"] for factor in result.factor_dicts}
