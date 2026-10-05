"""异常检测与合并/去抖（§8.4）：停滞建单、ETA 违约建单、去抖窗口内只刷新、合并计数与升级。"""

from __future__ import annotations

from datetime import datetime, timedelta

from app.core.clock import state as clock_state
from app.models.enums import DetectionRule, ExceptionStatus, ExceptionType
from app.rules import detection
from app.services import detection_flow
from tests.unit import _support

NOW = datetime(2026, 9, 30, 11, 0)


def test_stall_rule_threshold_boundary():
    below = detection.decide(
        now=NOW,
        order_status="IN_TRANSIT",
        last_move_at=NOW - timedelta(minutes=119),
        stall_threshold_minutes=120,
    )
    assert below.should_create is False

    at = detection.decide(
        now=NOW,
        order_status="IN_TRANSIT",
        last_move_at=NOW - timedelta(minutes=120),
        stall_threshold_minutes=120,
    )
    assert at.should_create is True
    assert at.rule == str(DetectionRule.STALL_OVER_THRESHOLD)
    assert at.exception_type == str(ExceptionType.VEHICLE_BREAKDOWN)


def test_not_in_transit_never_detects():
    decision = detection.decide(
        now=NOW,
        order_status="DISPATCHED",
        last_move_at=NOW - timedelta(hours=5),
        stall_threshold_minutes=120,
    )
    assert decision.should_create is False
    assert decision.rule is None


def test_in_transit_eta_breach_no_longer_creates_case():
    """2026-10-05：在途**不再**按预测 ETA 建延误单（旧的 ETA_BREACH_SLA 规则已下线）。

    延误只在订单送达时按"实际送达 − 承诺送达"判定（见 tests/test_delivered_at_settlement.py）。
    """
    decision = detection.decide(
        now=NOW,
        order_status="IN_TRANSIT",
        last_move_at=NOW - timedelta(minutes=5),  # 未停滞
        stall_threshold_minutes=120,
    )
    assert decision.rule is None
    assert decision.should_create is False
    assert "延误" in decision.reason


def test_debounce_window_helper():
    assert detection.is_debounced(now=NOW, last_detected_at=None, debounce_minutes=30) is False
    assert detection.is_debounced(now=NOW, last_detected_at=NOW - timedelta(minutes=29), debounce_minutes=30)
    assert not detection.is_debounced(
        now=NOW, last_detected_at=NOW - timedelta(minutes=31), debounce_minutes=30
    )


def test_automatic_case_stops_at_detected_with_rule_level(db_session, bootstrap):
    repos = _support.repos_for(db_session, bootstrap)
    case = _support.detected_exception(repos, bootstrap, stall_minutes=130)
    assert case.status == str(ExceptionStatus.DETECTED)
    assert case.detected_by == "SYSTEM"
    assert case.detection_rule == str(DetectionRule.STALL_OVER_THRESHOLD)
    assert case.type == str(ExceptionType.VEHICLE_BREAKDOWN)
    # VIP-01：车辆故障单 = 车辆故障 1 + VIP 1 = 2 → MEDIUM（新版不再叠加延误/违约分）
    assert case.risk_score == 2
    assert case.level == "MEDIUM"
    assert case.merged_count == 0
    events, _ = repos.exception_events.list_for_case(case.id)
    assert [event.event_type for event in events] == ["DETECTED"]


def test_second_hit_merges_and_debounces(db_session, bootstrap):
    repos = _support.repos_for(db_session, bootstrap)
    case = _support.detected_exception(repos, bootstrap, stall_minutes=130)
    order = repos.orders.get(case.order_id)

    # 去抖窗口内（10 分钟后）只刷新，不累计 merged_count，只写 COMMENT
    clock_state.advance(10)
    detection_flow.detect_for_order(repos, order)
    assert case.merged_count == 0
    events, _ = repos.exception_events.list_for_case(case.id)
    assert [event.event_type for event in events] == ["DETECTED", "COMMENT"]

    # 超出 30 分钟去抖窗口 → 合并 merged_count+1 并写 DETECTED
    clock_state.advance(60)
    detection_flow.detect_for_order(repos, order)
    assert case.merged_count == 1
    events, _ = repos.exception_events.list_for_case(case.id)
    assert [event.event_type for event in events] == ["DETECTED", "COMMENT", "DETECTED"]

    # 同一订单始终只有一张主单
    _, total = repos.exceptions.list(page=1, page_size=10)
    assert total == 1


def test_in_transit_eta_breach_does_not_touch_existing_case(db_session, bootstrap):
    """在途 ETA 违约不再触发检测（2026-10-05）：已有单不会被合并、也不会被"升级"成车辆故障。"""
    repos = _support.repos_for(db_session, bootstrap)
    order = _support.in_transit(repos, bootstrap, customer="normal")

    from app.services.exceptions import ExceptionService
    from app.services.orders import OrderService

    case = ExceptionService(repos).create_manual(
        order_id=order.id,
        type=str(ExceptionType.DELAY_RISK),
        occurred_at=bootstrap["base_time"],
        note="手工建单：预计延误",
    )
    assert case.type == str(ExceptionType.DELAY_RISK)

    clock_state.advance(10)
    OrderService(repos).update_eta(
        order.id,
        eta_at=bootstrap["base_time"] + timedelta(hours=31),
        reason="承运商反馈延后",
    )
    decision = detection_flow.evaluate_detection(repos, repos.orders.get(order.id))
    assert decision.rule is None  # 在途只检测停滞；延误在送达时判定
    assert decision.should_create is False

    clock_state.advance(60)
    assert detection_flow.detect_for_order(repos, repos.orders.get(order.id)) is None
    assert case.merged_count == 0
    assert case.type == str(ExceptionType.DELAY_RISK)  # 不会被"升级"改写类型
