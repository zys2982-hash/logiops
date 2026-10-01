"""tests/ai 专用脚手架：在共享 bootstrap 之上构造 CASE-A（§13.3 固定台词）。"""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.models.ai import AiAnalysis
from app.models.exception import CarrierMessage, ExceptionCase
from app.models.transport import Order, TrackingEvent
from app.repositories import Repos

CASE_A_ORDER_NO = "SO20260930021"
CASE_A_MESSAGE = "车在济南爆胎了，正在修，师傅说晚上8点能好"
CASE_A_DELAY_MINUTES = 270


def naive(value):
    return value.replace(tzinfo=None) if value is not None and value.tzinfo is not None else value


@pytest.fixture()
def repos(db_session, bootstrap) -> Repos:
    return Repos(db_session, bootstrap["workspace_id"])


@pytest.fixture()
def case_a(db_session, bootstrap, base_time) -> dict:
    """CASE-A：VIP 客户 + 车辆故障 + SLA 违约 270 分钟 → risk_score=4 CRITICAL。"""
    workspace_id = bootstrap["workspace_id"]
    promised = naive(base_time + timedelta(hours=24))
    expected_eta = naive(base_time + timedelta(hours=24, minutes=CASE_A_DELAY_MINUTES))

    order = Order(
        workspace_id=workspace_id,
        order_no=CASE_A_ORDER_NO,
        customer_id=bootstrap["customers"]["vip"].id,
        carrier_id=bootstrap["carrier"].id,
        vehicle_id=bootstrap["vehicle"].id,
        driver_id=bootstrap["driver"].id,
        origin_city="天津",
        dest_city="上海",
        distance_km=1200,
        status="IN_TRANSIT",
        dispatched_at=naive(base_time),
        promised_delivery_at=promised,
        original_eta_at=promised,
        current_eta_at=expected_eta,
    )
    db_session.add(order)
    db_session.flush()

    events = [
        TrackingEvent(
            workspace_id=workspace_id,
            order_id=order.id,
            event_type="DEPART",
            city="天津",
            occurred_at=naive(base_time - timedelta(hours=3)),
            source="MOCK",
            speed_kmh=62,
        ),
        TrackingEvent(
            workspace_id=workspace_id,
            order_id=order.id,
            event_type="STOP",
            city="济南",
            occurred_at=naive(base_time - timedelta(hours=1)),
            source="DRIVER",
            speed_kmh=0,
        ),
        TrackingEvent(
            workspace_id=workspace_id,
            order_id=order.id,
            event_type="REPAIR_START",
            city="济南",
            occurred_at=naive(base_time - timedelta(minutes=30)),
            source="CARRIER",
            speed_kmh=0,
        ),
    ]
    db_session.add_all(events)
    db_session.flush()

    case = ExceptionCase(
        workspace_id=workspace_id,
        case_no="EX20260930001",
        order_id=order.id,
        customer_id=bootstrap["customers"]["vip"].id,
        vehicle_id=bootstrap["vehicle"].id,
        carrier_id=bootstrap["carrier"].id,
        type="VEHICLE_BREAKDOWN",
        level="CRITICAL",
        status="CONFIRMING",
        detected_by="SYSTEM",
        detection_rule="STALL_OVER_THRESHOLD",
        occurred_at=naive(base_time),
        stall_since=naive(base_time - timedelta(hours=1)),
        root_cause_code="VEHICLE_BREAKDOWN",
        impact_summary="车辆在济南爆胎，等待维修",
        promised_delivery_at=promised,
        expected_eta_at=expected_eta,
        sla_delay_minutes=CASE_A_DELAY_MINUTES,
        sla_breached=True,
        risk_score=4,
    )
    db_session.add(case)
    db_session.flush()

    message = CarrierMessage(
        workspace_id=workspace_id,
        exception_id=case.id,
        order_id=order.id,
        channel="MANUAL_PASTE",
        sender_name="赵队长",
        raw_text=CASE_A_MESSAGE,
        received_at=naive(base_time + timedelta(minutes=5)),
        parse_status="PENDING",
    )
    db_session.add(message)
    db_session.flush()

    analysis = AiAnalysis(
        workspace_id=workspace_id,
        exception_id=case.id,
        analysis_no="AI20260930001",
        task_type="ANALYZE_EXCEPTION",
        status="PENDING",
        triggered_by=bootstrap["users"]["OPERATOR"].id,
    )
    db_session.add(analysis)
    db_session.commit()

    return {
        "workspace_id": workspace_id,
        "order": order,
        "order_id": order.id,
        "events": events,
        "event_ids": [event.id for event in events],
        "case": case,
        "exception_id": case.id,
        "message": message,
        "message_id": message.id,
        "analysis": analysis,
        "analysis_id": analysis.id,
        "promised_delivery_at": promised,
        "expected_eta_at": expected_eta,
        "delay_minutes": CASE_A_DELAY_MINUTES,
    }
