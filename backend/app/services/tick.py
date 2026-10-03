"""演示时钟推进编排（基线文档 §13.4）。

POST /demo/actions/tick {minutes} 期间自动执行：
    轨迹生成 → ETA 重算 → 异常检测 → 审批过期检查 → 自动关闭检查
POST /demo/actions/advance-to-less 内部循环 tick 直到订单送达并触发自动关闭。

注意：run_tick 会推进全局业务时钟（ReplayClock），因此测试里要 clock_state.reset()。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.core.clock import now_utc
from app.core.clock import state as clock_state
from app.models.enums import (
    AuditSource,
    ExceptionStatus,
    OrderStatus,
    TrackingEventType,
    TrackingSource,
    VehicleStatus,
)
from app.models.exception import ExceptionCase
from app.models.transport import Order
from app.repositories import Repos
from app.services import detection_flow, eta_flow
from app.services.approvals import ApprovalExecutor
from app.services.common import now_naive, to_naive_utc
from app.services.exceptions import ExceptionService
from app.services.orders import OrderService

IN_TRANSIT = str(OrderStatus.IN_TRANSIT)
DEFAULT_PROGRESS_STEP = 0.05
DEFAULT_REPAIR_MINUTES = 120
MOCK_SPEED_KMH = 45.0


def progress_delta(order: Order, minutes: int, progress_step: float | None) -> float:
    """模拟推进量：显式 progress_step 优先；否则按"模拟时长 × 车速 / 总里程"换算。

    这样 `tick {minutes: 大值}` 真能把车辆推到终点（Lead 验收口径），60 分钟的小步长则缓慢推进。
    """
    if progress_step is not None:
        return max(progress_step, 0.0)
    if not order.distance_km:
        return DEFAULT_PROGRESS_STEP
    covered_km = MOCK_SPEED_KMH * max(minutes, 0) / 60.0
    return min(1.0, covered_km / float(order.distance_km))


def repair_recovery_at(repos: Repos, order: Order) -> datetime | None:
    """维修完成时间（委托 eta_flow，保持"任何重算路径都保留维修等待"的单一真相）。"""
    return eta_flow.resolve_repair_recovery(repos, order)


def _orders_for_tick(repos: Repos, focus_order_id: int | None) -> list[Order]:
    if focus_order_id is None:
        return repos.orders.all(filters=[Order.status == IN_TRANSIT])
    order = repos.orders.get(focus_order_id)
    if order is None or str(order.status) != IN_TRANSIT:
        return []
    return [order]


def _generate_tracking(
    repos: Repos,
    *,
    progress_step: float | None,
    minutes: int,
    orders: list[Order],
) -> dict[str, Any]:
    """模拟轨迹推进。

    到达即送达（硬约束）：行程比例到 1（payload 或最后位置即目的城市）时，无论车辆是否
    登记为维修中，都先走状态机送达，避免"ETA=now、剩余 0，但订单仍 IN_TRANSIT"。
    维修中的车辆在恢复时间到达后先发 REPAIR_END，再按进度推进。
    """
    service = OrderService(repos)
    now = now_naive()
    events = 0
    delivered: list[int] = []
    moved: set[int] = set()
    for order in orders:
        vehicle = repos.vehicles.get(order.vehicle_id) if order.vehicle_id else None
        if eta_flow.arrival_reached(repos, order):
            service.append_tracking(
                order.id,
                event_type=str(TrackingEventType.DELIVER),
                city=order.dest_city,
                source=str(TrackingSource.MOCK),
                payload={"progress_ratio": 1.0, "mock": True},
            )
            delivered.append(order.id)
            moved.add(order.id)
            events += 1
            continue

        if vehicle is not None and str(vehicle.status) == str(VehicleStatus.REPAIRING):
            recovery = repair_recovery_at(repos, order)
            if recovery is None or now < recovery:
                continue  # 修理未完成：保持停滞现场
            service.append_tracking(
                order.id,
                event_type=str(TrackingEventType.REPAIR_END),
                city=vehicle.current_city or order.origin_city,
                source=str(TrackingSource.MOCK),
                payload={"progress_ratio": eta_flow.progress_ratio(repos, order), "mock": True},
            )
            moved.add(order.id)
            events += 1
            # 同一 tick 内继续按模拟时长推进（大步长 tick 才能真正"到站即送达"）

        ratio = eta_flow.progress_ratio(repos, order)
        target = min(1.0, ratio + progress_delta(order, minutes, progress_step))
        moved.add(order.id)
        if target >= 1.0:
            service.append_tracking(
                order.id,
                event_type=str(TrackingEventType.DELIVER),
                city=order.dest_city,
                source=str(TrackingSource.MOCK),
                payload={"progress_ratio": 1.0, "mock": True},
            )
            delivered.append(order.id)
        else:
            city = order.origin_city
            if vehicle is not None and vehicle.current_city:
                city = vehicle.current_city
            stop = target >= 0.5 and ratio < 0.5
            service.append_tracking(
                order.id,
                event_type=str(TrackingEventType.STOP) if stop else str(TrackingEventType.NOTE),
                city=city,
                source=str(TrackingSource.MOCK),
                speed_kmh=45.0,
                payload={"progress_ratio": target, "mock": True},
            )
        events += 1
    return {"events": events, "delivered_orders": delivered, "moved": moved}


def _recalc_and_detect(repos: Repos, moved: set[int], orders: list[Order]) -> dict[str, Any]:
    service = OrderService(repos)
    eta_updates = 0
    touched: list[int] = []
    for order in orders:
        if order.id not in moved:
            service.recalc_eta(order, source=AuditSource.SYSTEM)
            eta_updates += 1
        case = detection_flow.detect_for_order(repos, order, moment=now_naive())
        if case is not None:
            touched.append(case.id)
    return {"eta_updates": eta_updates, "exceptions_touched": sorted(set(touched))}


def _resolve_cleared_risks(repos: Repos, orders: list[Order]) -> list[int]:
    """§8.2：PROCESSING + 新轨迹恢复且规则判定风险解除 → RESOLVED（不产生"降级但仍挂着"的中间态）。"""
    service = ExceptionService(repos)
    resolved: list[int] = []
    for order in orders:
        if str(order.status) != IN_TRANSIT:
            continue
        case = repos.exceptions.find_open_by_order(order.id)
        if case is None or str(case.status) != str(ExceptionStatus.PROCESSING):
            continue
        # 只升不降（与 eta_flow 文档口径一致）：SLA 数字按事实刷新，但绝不"系统悄悄把高危降级"；
        # 风险真解除时由下面的 resolve 收口，而不是留一张"降级但还挂着"的单
        eta_flow.refresh_case_impact(repos, case, order, eta_at=order.current_eta_at, allow_downgrade=False)
        if not bool(case.sla_breached):
            service.resolve(
                case.id,
                note="新轨迹恢复，规则判定风险已解除（系统自动解除异常）",
                expected_version=None,
                actor_id=None,
                system=True,
            )
            resolved.append(case.id)
    return resolved


def run_tick(
    session: Session,
    repos: Repos,
    *,
    workspace_id: int | None = None,
    minutes: int = 60,
    progress_step: float | None = None,
    focus_order_id: int | None = None,
) -> dict[str, Any]:
    """推进业务时钟一个步长；focus_order_id 非空时只对目标订单做轨迹推进与检测。"""
    repos = repos or Repos(session, workspace_id)
    clock_state.advance(minutes)

    orders = _orders_for_tick(repos, focus_order_id)
    before_count = repos.exceptions.count()
    tracking = _generate_tracking(
        repos, progress_step=progress_step, minutes=minutes, orders=orders
    )
    flow = _recalc_and_detect(repos, tracking["moved"], orders)
    risk_cleared = _resolve_cleared_risks(repos, orders)
    expired = ApprovalExecutor(repos).expire_stale()

    exception_service = ExceptionService(repos)
    closed_exceptions = exception_service.auto_close_resolved()
    closed_orders = OrderService(repos).auto_close_delivered()
    after_count = repos.exceptions.count()

    return {
        "minutes": minutes,
        "now_utc": now_utc().isoformat(),
        "offset_minutes": clock_state.offset_minutes,
        "tracking_events": tracking["events"],
        "delivered_orders": tracking["delivered_orders"],
        "eta_updates": flow["eta_updates"],
        "exceptions_touched": flow["exceptions_touched"],
        "exceptions_created": max(after_count - before_count, 0),
        "exceptions_resolved": risk_cleared,
        "approvals_expired": expired,
        "exceptions_closed": closed_exceptions,
        "orders_closed": closed_orders,
    }


def _pick_target(repos: Repos, target_order_id: int | None) -> tuple[Order | None, Any | None]:
    """确定性目标选择（不受等级重算影响）：

    1. 优先"脚本化案例"——有承运商消息的未关闭异常（§13.3 CASE-A），并列取 created_at/id 最小；
    2. 否则取最近创建的未关闭异常（并列取 created_at/id 最大）；
    3. 全部已关闭时退化为最近更新的一条，仅用于汇报当前状态（保证重复调用幂等）。
    """
    if target_order_id is not None:
        order = repos.orders.get(target_order_id)
        if order is None:
            return None, None
        return order, repos.exceptions.find_open_by_order(order.id)

    # 1) 脚本化案例（带承运商消息，§13.3 CASE-A）：含已关闭者，保证重复调用直接返回 ALREADY_CLOSED
    all_cases = repos.exceptions.all(
        order_by=[ExceptionCase.created_at.asc(), ExceptionCase.id.asc()]
    )
    scripted = [case for case in all_cases if repos.messages.latest_for_case(case.id) is not None]
    if scripted:
        order = repos.orders.get(scripted[0].order_id)
        if order is not None:
            return order, scripted[0]

    # 2) 否则取最近创建的未关闭异常
    candidates = sorted(
        repos.exceptions.list_open(),
        key=lambda case: (case.created_at or case.id, case.id),
        reverse=True,
    )
    for case in candidates:
        order = repos.orders.get(case.order_id)
        if order is not None:
            return order, case

    latest = repos.exceptions.all(order_by=[ExceptionCase.updated_at.desc(), ExceptionCase.id.desc()])[:1]
    if latest:
        order = repos.orders.get(latest[0].order_id)
        if order is not None:
            return order, latest[0]
    return None, None


def _report(
    repos: Repos,
    order: Order | None,
    case: Any | None,
    *,
    ticks: int,
    advanced: int,
    stopped_reason: str,
) -> dict[str, Any]:
    final_order = repos.orders.get(order.id) if order is not None else None
    final_case = repos.exceptions.get(case.id) if case is not None else None
    return {
        "target_order_no": final_order.order_no if final_order else None,
        "target_exception_no": final_case.case_no if final_case else None,
        "ticks": ticks,
        "advanced_minutes": advanced,
        "final_order_status": final_order.status if final_order else None,
        "final_exception_status": final_case.status if final_case else None,
        "stopped_reason": stopped_reason,
        "offset_minutes": clock_state.offset_minutes,
        "now_utc": now_utc().isoformat(),
    }


def advance_until_delivered(
    session: Session,
    repos: Repos,
    *,
    workspace_id: int | None = None,
    target_order_id: int | None = None,
    minutes: int = 30,
    progress_step: float | None = None,
    max_minutes: int = 72 * 60,
    close_after_hours: int = 24,
) -> dict[str, Any]:
    """把"门面案例"推到送达 + 异常 CLOSED（含送达后 24h 自动关闭）。

    - 未指定 target_order_id 时，锁定"未关闭异常中最早创建者"（Demo 主案例 = CASE-A）。
    - 幂等：目标已 CLOSED 时直接返回当前状态。
    - 等维修恢复 / 等 24h 自动关闭时直接跳跃步长，避免无意义空转。
    """
    repos = repos or Repos(session, workspace_id)
    order, case = _pick_target(repos, target_order_id)
    if order is None:
        return _report(repos, None, None, ticks=0, advanced=0, stopped_reason="NO_TARGET")

    if case is not None and str(case.status) == str(ExceptionStatus.CLOSED):
        return _report(repos, order, case, ticks=0, advanced=0, stopped_reason="ALREADY_CLOSED")

    ticks = 0
    advanced = 0
    stopped_reason = "MAX_MINUTES"
    settled_after_delivery = False
    while advanced < max_minutes:
        order = repos.orders.get(order.id)
        case = repos.exceptions.get(case.id) if case is not None else None
        if order is None:
            stopped_reason = "NO_TARGET"
            break

        status_now = str(order.status)
        if status_now in {str(OrderStatus.DELIVERED), str(OrderStatus.CLOSED)}:
            if case is None:
                stopped_reason = "TARGET_DELIVERED"
                break
            delivered_at = to_naive_utc(order.delivered_at)
            if not settled_after_delivery and status_now == str(OrderStatus.DELIVERED) and delivered_at:
                # 只差"送达后 24h 自动关闭"：直接跳到截止时刻
                deadline = delivered_at + timedelta(hours=close_after_hours, minutes=1)
                gap = int((deadline - now_naive()).total_seconds() // 60)
                step = max(minutes, min(gap, max_minutes - advanced))
                if step <= 0:
                    break
                run_tick(
                    session,
                    repos,
                    workspace_id=workspace_id,
                    minutes=step,
                    progress_step=0.0,
                    focus_order_id=order.id,
                )
                ticks += 1
                advanced += step
                settled_after_delivery = True
                continue
            # 送达 24h 已推过：给终局结论（DETECTED/CONFIRMING/ANALYZING 需人工推进，不能自动关闭）
            stopped_reason = (
                "TARGET_CLOSED" if str(case.status) == str(ExceptionStatus.CLOSED) else "EXCEPTION_NEEDS_MANUAL_STEP"
            )
            break

        step = min(minutes, max_minutes - advanced)
        step_progress = progress_step
        vehicle = repos.vehicles.get(order.vehicle_id) if order.vehicle_id else None
        if vehicle is not None and str(vehicle.status) == str(VehicleStatus.REPAIRING):
            recovery = repair_recovery_at(repos, order)
            if recovery is not None and recovery > now_naive():
                gap = int((recovery - now_naive()).total_seconds() // 60) + 1
                step = max(1, min(gap, max_minutes - advanced))
        if step <= 0:
            break
        run_tick(
            session,
            repos,
            workspace_id=workspace_id,
            minutes=step,
            progress_step=step_progress,
            focus_order_id=order.id,
        )
        ticks += 1
        advanced += step

    final_order = repos.orders.get(order.id) if order is not None else None
    final_case = repos.exceptions.get(case.id) if case is not None else None
    if final_case is not None and str(final_case.status) == str(ExceptionStatus.CLOSED):
        stopped_reason = "TARGET_CLOSED"
    elif (
        stopped_reason == "MAX_MINUTES"
        and final_order is not None
        and str(final_order.status) in {str(OrderStatus.DELIVERED), str(OrderStatus.CLOSED)}
    ):
        stopped_reason = "TARGET_DELIVERED" if final_case is None else "EXCEPTION_NEEDS_MANUAL_STEP"
    return _report(repos, order, case, ticks=ticks, advanced=advanced, stopped_reason=stopped_reason)


__all__ = ["advance_until_delivered", "repair_recovery_at", "run_tick"]
