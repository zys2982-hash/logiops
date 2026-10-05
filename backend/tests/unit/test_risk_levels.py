"""风险等级四档（§8.6，2026-10-05 新模型）。

新口径：**一张异常单只受一个问题影响** ——
· 车辆故障单 = 车辆故障(1) + 客户等级（VIP 1 / SVIP 2），**不计延误、不计违约**；
· 延误单 = 延误档位（≤120=1 / 121–360=2 / >360=3）+ 客户等级，封顶 4（不再重复加"违约 1"）。
"""

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


def test_delay_25_minutes_is_medium():
    result = risk.evaluate_risk(delay_minutes=25, customer_level="NORMAL", exception_type="DELAY_RISK")
    assert result.score == 1
    assert result.level == str(ExceptionLevel.MEDIUM)
    assert {factor["code"] for factor in result.factor_dicts} == {"DELAY_BASE"}


def test_high_when_score_three():
    """延误 121–360min（2 分）+ VIP 1 → 3 → HIGH；不再额外加违约分。"""
    result = risk.evaluate_risk(delay_minutes=200, customer_level="VIP", exception_type="DELAY_RISK")
    assert result.score == 3
    assert result.level == str(ExceptionLevel.HIGH)


def test_vehicle_case_is_immune_to_delay_and_breach():
    """CASE-A：车辆故障单 + VIP → 1 + 1 = 2 MEDIUM（延误/违约都不参与）。"""
    result = risk.evaluate_risk(
        delay_minutes=270,
        customer_level="VIP",
        exception_type="VEHICLE_BREAKDOWN",
    )
    assert result.score == 2
    assert result.level == str(ExceptionLevel.MEDIUM)
    assert {factor["code"] for factor in result.factor_dicts} == {"VEHICLE_BREAKDOWN", "CUSTOMER_VIP"}


def test_delay_case_capped_at_four():
    """延误 >360min（3 分）+ VIP 1 = 4 → CRITICAL（封顶 4）。"""
    result = risk.evaluate_risk(delay_minutes=400, customer_level="VIP", exception_type="DELAY_RISK")
    assert result.score == 4
    assert result.level == str(ExceptionLevel.CRITICAL)
    assert {factor["code"] for factor in result.factor_dicts} == {"DELAY_BASE", "CUSTOMER_VIP"}


def test_vehicle_case_repairing_flag_controls_factor():
    """车辆已恢复（vehicle_repairing=False）→ 车辆故障因子不再计分。"""
    result = risk.evaluate_risk(
        customer_level="VIP", exception_type="VEHICLE_BREAKDOWN", vehicle_repairing=False
    )
    assert result.score == 1
    assert {factor["code"] for factor in result.factor_dicts} == {"CUSTOMER_VIP"}


def test_svip_adds_two_points():
    result = risk.evaluate_risk(delay_minutes=0, customer_level="SVIP", exception_type="DELAY_RISK")
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
        vip_upgrade=False,
    )
    assert result.score == 1  # 只有车辆故障 1 分（VIP 放大被关掉）
    assert "CUSTOMER_VIP" not in {factor["code"] for factor in result.factor_dicts}
