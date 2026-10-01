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


def test_eta_breach_rule_uses_allowance():
    decision = detection.decide(
        now=NOW,
        order_status="IN_TRANSIT",
        last_move_at=NOW - timedelta(minutes=5),
        stall_threshold_minutes=120,
        expected_eta_at=NOW + timedelta(hours=2),
        promised_delivery_at=NOW + timedelta(hours=1),
        max_delay_minutes=30,
    )
    assert decision.rule == str(DetectionRule.ETA_BREACH_SLA)
    assert decision.exception_type == str(ExceptionType.DELAY_RISK)


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
    # VIP-01：停滞 130min → 基础 2 + VIP 1 + 故障 1 = 4 → CRITICAL
    assert case.risk_score == 4
    assert case.level == "CRITICAL"
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


def test_eta_breach_merge_upgrades_type(db_session, bootstrap):
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

    # NORMAL 客户允许 30min：把 ETA 推到承诺 +1h → 命中的是 ETA_BREACH_SLA
    clock_state.advance(10)
    OrderService(repos).update_eta(
        order.id,
        eta_at=bootstrap["base_time"] + timedelta(hours=31),
        reason="承运商反馈延后",
    )
    decision = detection_flow.evaluate_detection(repos, repos.orders.get(order.id))
    assert decision.rule == str(DetectionRule.ETA_BREACH_SLA)
    assert decision.merge_only is True

    detection_flow.detect_for_order(repos, repos.orders.get(order.id))  # 去抖窗口内
    assert case.merged_count == 0

    clock_state.advance(60)
    merged = detection_flow.detect_for_order(repos, repos.orders.get(order.id))
    assert merged is not None
    assert merged.id == case.id
    assert merged.type == str(ExceptionType.VEHICLE_BREAKDOWN)  # §8.4 合并升级
    assert merged.merged_count == 1
