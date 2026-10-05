"""tests/unit 共用脚手架：构造订单 / 轨迹 / 异常的最小数据（不依赖 seed 与 AI 层）。"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.models.ai import AiAnalysis, Approval
from app.models.exception import ExceptionCase
from app.models.transport import Order, TrackingEvent
from app.repositories import Repos
from app.services.common import next_analysis_no, now_naive
from app.services.orders import OrderService


def repos_for(db_session: Session, bootstrap: dict[str, Any]) -> Repos:
    return Repos(db_session, workspace_id=bootstrap["workspace_id"])


def new_order(
    repos: Repos,
    bootstrap: dict[str, Any],
    *,
    customer: str = "vip",
    distance_km: int = 1000,
    order_no: str | None = None,
) -> Order:
    return OrderService(repos).create(
        customer_id=bootstrap["customers"][customer].id,
        origin_city="天津",
        dest_city="上海",
        order_no=order_no,
        distance_km=distance_km,
    )


def dispatch(repos: Repos, bootstrap: dict[str, Any], order: Order) -> Order:
    return OrderService(repos).dispatch(
        order.id,
        carrier_id=bootstrap["carrier"].id,
        vehicle_id=bootstrap["vehicle"].id,
        driver_id=bootstrap["driver"].id,
    )


def track(
    repos: Repos,
    order: Order,
    *,
    event_type: str = "DEPART",
    city: str = "天津",
    occurred_at: datetime | None = None,
    speed_kmh: float | None = None,
    payload: dict[str, Any] | None = None,
) -> TrackingEvent:
    return OrderService(repos).append_tracking(
        order.id,
        event_type=event_type,
        city=city,
        occurred_at=occurred_at,
        speed_kmh=speed_kmh,
        payload=payload,
    )


def in_transit(
    repos: Repos,
    bootstrap: dict[str, Any],
    *,
    customer: str = "vip",
    distance_km: int = 200,
    departed_at: datetime | None = None,
    speed_kmh: float | None = None,
) -> Order:
    """建单 → 派车 → 首条 DEPART，返回 IN_TRANSIT 订单。

    默认 200km / 兜底 40km/h：初始 ETA（约 2h 后）远早于承诺时间，避免首条轨迹
    就命中 ETA_BREACH_SLA，让单测能单独验证 STALL 规则。
    """
    order = new_order(repos, bootstrap, customer=customer, distance_km=distance_km)
    dispatch(repos, bootstrap, order)
    track(
        repos,
        order,
        event_type="DEPART",
        city=order.origin_city,
        occurred_at=departed_at or bootstrap["base_time"],
        speed_kmh=speed_kmh,
    )
    return repos.orders.get(order.id)


def detected_exception(
    repos: Repos,
    bootstrap: dict[str, Any],
    *,
    stall_minutes: int = 130,
    customer: str = "vip",
    delay_minutes: int = 180,
) -> ExceptionCase:
    """构造一条命中 STALL 规则的自动异常（DETECTED）。

    2026-10-05 新模型下在途只检测停滞，且"车辆故障"因子按**车辆现状**计——
    这里按 CASE-A 的真实故事把车辆置为「维修中」，因子才是 车辆故障(1) + 客户等级，
    风险分不再受 ETA/违约影响（delay_minutes 仍写进 ETA 快照，只作订单事实）。
    """
    from app.core.clock import state as clock_state
    from app.services import detection_flow, eta_flow

    order = in_transit(repos, bootstrap, customer=customer)
    _, promised = eta_flow.resolve_promised_at(repos, order)
    assert promised is not None
    OrderService(repos).update_eta(
        order.id,
        eta_at=promised + timedelta(minutes=delay_minutes),
        reason="单测构造：把 ETA 推到违约分档（仅作订单事实，不影响风险分）",
    )
    if order.vehicle_id:
        vehicle = repos.vehicles.get(order.vehicle_id)
        if vehicle is not None:
            vehicle.status = "REPAIRING"
            repos.vehicles.save(vehicle)
    clock_state.advance(stall_minutes)
    case = detection_flow.detect_for_order(repos, order)
    assert case is not None, "数据构造失败：未命中停滞规则"
    return case


def make_analysis(
    repos: Repos,
    case: ExceptionCase,
    *,
    output: dict[str, Any] | None = None,
    status: str = "READY",
) -> AiAnalysis:
    """直接造一条分析结果（单测不依赖 AI 层）。"""
    analysis = AiAnalysis(
        workspace_id=int(repos.workspace_id or 0),
        exception_id=case.id,
        analysis_no=next_analysis_no(repos),
        task_type="ANALYZE_EXCEPTION",
        status=status,
        input_hash="unit-hash",
        model="deepseek-chat",
        prompt_version="v1",
        output_json=output,
        risk_level_calculated=case.level,
        finished_at=now_naive() if status == "READY" else None,
    )
    repos.analyses.add(analysis)
    return analysis


def make_approval(
    repos: Repos,
    case: ExceptionCase,
    *,
    action: str = "UPDATE_ETA",
    ai_payload: dict[str, Any] | None = None,
    analysis: AiAnalysis | None = None,
) -> Approval:
    approval = Approval(
        workspace_id=int(repos.workspace_id or 0),
        exception_id=case.id,
        analysis_id=analysis.id if analysis else None,
        action_type=action,
        ai_payload_json=ai_payload or {},
        status="PENDING",
    )
    repos.approvals.add(approval)
    return approval


def ai_output(
    *,
    delay_minutes: int = 270,
    sla_breached: bool = True,
    suggestions: list[dict[str, Any]] | None = None,
    expected_eta_at: str | None = None,
) -> dict[str, Any]:
    return {
        "summary": "车辆在济南维修，预计晚到。",
        "root_cause": {"code": "VEHICLE_BREAKDOWN", "note": "承运商反馈爆胎"},
        "impact": {
            "delay_minutes": delay_minutes,
            "sla_breached": sla_breached,
            "expected_eta_at": expected_eta_at,
            "affected_customer_level": "VIP",
        },
        "suggestions": suggestions
        if suggestions is not None
        else [
            {"code": "UPDATE_ETA", "title": "更新 ETA", "rationale": "按恢复时间估算"},
            {"code": "CREATE_FOLLOWUP", "title": "20:30 回访承运商", "rationale": "确认恢复"},
            {"code": "SAVE_NOTICE", "title": "生成延误通知", "rationale": "VIP 需 30 分钟内告知"},
        ],
        "open_questions": ["配件是否到位？"],
        "evidence_refs": [{"type": "CARRIER_MESSAGE", "id": 1, "note": "济南爆胎"}],
    }
