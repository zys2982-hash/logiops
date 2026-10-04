"""ETA 重算 → SLA 判定 → 风险定级的编排（纯规则在 app.rules.*，这里只负责取数与落库）。

基线文档 §8.3 / §8.5 / §8.6：
- promised_delivery_at 由 SLA 规则算；sla_delay = expected_eta - promised；
- level 由规则决定，LLM 无权修改；
- ETA 必须输出 eta_method（REPAIR_WAIT / MOVING_AVG_SPEED / FALLBACK）以便单测与解释。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from app.core.config import get_settings
from app.models.enums import ExceptionStatus, ExceptionType, OrderStatus, VehicleStatus
from app.models.exception import ExceptionCase
from app.models.transport import Order
from app.repositories import Repos
from app.rules import eta as eta_rules
from app.rules import risk as risk_rules
from app.rules import sla as sla_rules
from app.services.common import bump_version, now_naive, parse_iso_naive, to_naive_utc

MAX_IMPACT_SUMMARY = 255


def progress_ratio(repos: Repos, order: Order) -> float:
    """行程比例：优先取轨迹 payload 里的模拟进度；无数据用 0.6 兜底（§8.5）。"""
    events = repos.tracking.list_for_order(order.id, limit=5)
    for event in events:
        payload = event.payload_json or {}
        if isinstance(payload, dict) and payload.get("progress_ratio") is not None:
            try:
                return min(max(float(payload["progress_ratio"]), 0.0), 1.0)
            except (TypeError, ValueError):
                break
    if order.dest_city and events and events[0].city == order.dest_city:
        return 1.0
    if order.origin_city and events and events[0].city == order.origin_city:
        return 0.0
    return eta_rules.DEFAULT_PROGRESS_RATIO


def match_sla(repos: Repos, order: Order) -> sla_rules.SlaMatch:
    customer = repos.customers.get(order.customer_id)
    return sla_rules.match_rule(
        repos.sla_rules.list_active(),
        customer_code=customer.code if customer else None,
        customer_level=customer.level if customer else None,
    )


def resolve_promised_at(repos: Repos, order: Order) -> tuple[sla_rules.SlaMatch, datetime | None]:
    match = match_sla(repos, order)
    promised = to_naive_utc(order.promised_delivery_at)
    if promised is None:
        promised = sla_rules.compute_promised_at(order.dispatched_at, match.deadline_offset_hours)
    return match, to_naive_utc(promised)


DEFAULT_REPAIR_MINUTES = 120


def resolve_repair_recovery(repos: Repos, order: Order) -> datetime | None:
    """维修完成时间：优先取承运商消息解析出的 estimated_recovery_at，
    否则按"异常发生后默认修 120 分钟"兜底（保证任何重算路径都保留维修等待）。"""
    case = repos.exceptions.find_open_by_order(order.id)
    if case is None:
        return None
    message = repos.messages.latest_for_case(case.id)
    if message is not None:
        parsed = message.parse_result_json or {}
        recovery = parse_iso_naive(parsed.get("estimated_recovery_at"))
        if recovery is not None:
            return recovery
    occurred = to_naive_utc(case.occurred_at)
    if occurred is None:
        return None
    return occurred + timedelta(minutes=DEFAULT_REPAIR_MINUTES)


def recalc_order_eta(
    repos: Repos,
    order: Order,
    *,
    repair_recovery_at: datetime | None = None,
    moment: datetime | None = None,
) -> eta_rules.EtaResult:
    """重算并写回 order.current_eta_at（不写审计，由调用方决定动作语义）。

    维修中的车辆必须保留"维修等待"口径：未显式传入恢复时间时自动从承运商消息推导，
    否则 ETA 会被算成"忽略维修、立刻起程"，导致等级被错误降档。
    """
    if str(order.status) == str(OrderStatus.DELIVERED):
        delivered_at = to_naive_utc(order.delivered_at) or now_naive()
        return eta_rules.EtaResult(
            eta_at=delivered_at,
            method=eta_rules.EtaMethod.FALLBACK,
            remaining_km=0.0,
            avg_speed_kmh=eta_rules.DEFAULT_SPEED_KMH,
            resume_at=delivered_at,
            detail="订单已送达，ETA 锁定为送达时间",
        )
    # ETA 重算总开关（docs/08：不再做速度模拟）。停用时沿用既有 ETA 快照，一处拦住所有调用方
    # （tick / 写轨迹 / 消息解析 / 送达都会走到这里），且不抛异常、不改变订单。
    from app.core.config import get_settings  # 局部导入，避免模块级循环依赖

    if not get_settings().eta_enabled:
        locked = to_naive_utc(order.current_eta_at) or to_naive_utc(order.promised_delivery_at) or now_naive()
        return eta_rules.EtaResult(
            eta_at=locked,
            method=eta_rules.EtaMethod.FALLBACK,
            remaining_km=0.0,
            avg_speed_kmh=eta_rules.DEFAULT_SPEED_KMH,
            resume_at=locked,
            detail="ETA 重算已停用（docs/08）：不再按车速/里程模拟到达时间，沿用既有 ETA 快照",
        )
    now = to_naive_utc(moment) or now_naive()
    events = repos.tracking.list_for_order(order.id, limit=50)
    recovery = to_naive_utc(repair_recovery_at)
    if recovery is None and vehicle_is_repairing(repos, order.vehicle_id):
        recovery = resolve_repair_recovery(repos, order)
    result = eta_rules.recalc(
        now=now,
        distance_km=order.distance_km,
        progress_ratio=progress_ratio(repos, order),
        repair_recovery_at=recovery,
        events=events,
    )
    order.current_eta_at = result.eta_at
    bump_version(order)
    repos.orders.save(order)
    return result


def arrival_reached(repos: Repos, order: Order) -> bool:
    """是否已到达：行程比例到 1（轨迹 payload 或最后一条位置即目的城市）。"""
    if str(order.status) != str(OrderStatus.IN_TRANSIT):
        return False
    return progress_ratio(repos, order) >= 1.0


def refresh_case_impact(
    repos: Repos,
    case: ExceptionCase,
    order: Order,
    *,
    eta_at: datetime | None = None,
    allow_downgrade: bool = True,
) -> dict[str, Any]:
    """按规则刷新异常单的 SLA 快照与风险等级（永远覆盖 LLM 的 level）。

    allow_downgrade=False：等级/风险分只升不降（用于 tick 的合并刷新）。
    机器提议阶段（DETECTED/CONFIRMING）不允许"高危单静默降档"，风险真的解除时
    由 PROCESSING → RESOLVED 或"送达即闭环"收口（Lead 验收口径）。
    """
    settings = get_settings()
    customer = repos.customers.get(case.customer_id)
    match, promised = resolve_promised_at(repos, order)
    expected = to_naive_utc(eta_at) if eta_at is not None else to_naive_utc(order.current_eta_at)
    if expected is None:
        expected = to_naive_utc(case.expected_eta_at)

    impact = sla_rules.evaluate(match, promised_delivery_at=promised, expected_eta_at=expected)
    risk = risk_rules.evaluate_risk(
        vehicle_repairing=vehicle_is_repairing(repos, order.vehicle_id),
        delay_minutes=impact.delay_minutes,
        customer_level=customer.level if customer else None,
        exception_type=case.type,
        sla_breached=impact.breached,
        vip_upgrade=settings.risk_vip_upgrade,
    )

    case.promised_delivery_at = promised
    case.expected_eta_at = expected
    case.sla_delay_minutes = impact.delay_minutes
    case.sla_breached = impact.breached
    current_score = int(case.risk_score or 0)
    if allow_downgrade or risk.score >= current_score:
        case.level = str(risk.level)
        case.risk_score = risk.score
        case.risk_factors_json = risk.factor_dicts
    if not (case.impact_summary or "").strip():
        case.impact_summary = summary_text(match, impact, risk)
    bump_version(case)
    repos.exceptions.save(case)
    return {
        "match": match,
        "impact": impact,
        "risk": risk,
        "promised_delivery_at": promised,
        "expected_eta_at": expected,
        "level_kept": (not allow_downgrade) and risk.score < current_score,
    }


def summary_text(match: sla_rules.SlaMatch, impact: sla_rules.SlaImpact, risk: risk_rules.RiskResult) -> str:
    verdict = "已违约" if impact.breached else "未违约"
    text = (
        f"{match.rule_name}：承诺 {impact.promised_delivery_at} / 预计 {impact.expected_eta_at}，"
        f"延误 {impact.delay_minutes} 分钟（{verdict}，允许 {match.max_delay_minutes} 分钟）；"
        f"风险 {risk.level}({risk.score})：{risk.explanation}"
    )
    return text[:MAX_IMPACT_SUMMARY]


def vehicle_is_repairing(repos: Repos, vehicle_id: int | None) -> bool:
    if not vehicle_id:
        return False
    vehicle = repos.vehicles.get(vehicle_id)
    return vehicle is not None and str(vehicle.status) == str(VehicleStatus.REPAIRING)


VEHICLE_BREAKDOWN_FACTOR = "VEHICLE_BREAKDOWN"
# 已结束的异常不做读取时自愈：RESOLVED / CLOSED 是"历史判定"，卡片按当时的口径存档
# （070a8f4：已结束的卡片不再声称"当前风险"）。与 services.exceptions 的
# VEHICLE_HOLDING_STATUSES 同口径（那边判断"车还占着维修状态"）。
ENDED_EXCEPTION_STATUSES: frozenset[str] = frozenset(
    {str(ExceptionStatus.RESOLVED), str(ExceptionStatus.CLOSED)}
)


def current_case_type(case: ExceptionCase) -> str:
    """异常单的**当前问题**（实时推导），与"建单原因" `case.type` 区分开。

    用户口径（2026-10-04）：「一个异常订单的异常是实时改变的，不要用类型固定他」——
    同一张单会随现实变化（车辆修好了、只剩延误/违约），所以界面该显示"现在是什么问题"：
    · 未结束（DETECTED / PROCESSING）：按风险因子实时推导 —— 还带「车辆故障」因子 → 车辆故障，
      否则 → 延误风险（延误时长 / SLA 违约 / VIP 都属延误口径）；
    · 已结束（RESOLVED / CLOSED）：没有"当前"了，直接沿用建单原因（历史判定，见 ENDED_EXCEPTION_STATUSES）。
    `case.type` 始终保留为建单原因（历史），两者不同时前端会一起标出来。
    """
    if str(case.status) in ENDED_EXCEPTION_STATUSES:
        return str(case.type)
    codes = {str(factor.get("code")) for factor in (case.risk_factors_json or []) if isinstance(factor, dict)}
    if VEHICLE_BREAKDOWN_FACTOR in codes:
        return str(ExceptionType.VEHICLE_BREAKDOWN)
    return str(ExceptionType.DELAY_RISK)


def sync_case_vehicle_factor(repos: Repos, case: ExceptionCase, order: Order | None = None) -> bool:
    """读取时自愈：把「车辆故障」因子对齐到订单车辆的**现状**（用户口径：卡片显示当前风险）。

    为什么不挂在写入侧：车辆状态有 5+ 个写入口（派车 / 维修 / 送达 / 异常驱动 / 消息解析），
    逐个挂钩容易漏；而异常列表与详情都要经过这里，一处即可保证"界面任何地方看到的都是现状"。
    实测口径：人工把车辆改成「维修中」→ 卡片出现「车辆故障」；改回空闲 → 因子消失。

    只做三件克制的事：
    1. 仅对**未结束**的异常生效（已结束的异常保持历史判定，见 ENDED_EXCEPTION_STATUSES）；
    2. 仅加/减「车辆故障」这一个因子，风险分与等级由**剩下的因子权重**重算（规则口径一致）；
    3. 不重算 SLA / ETA / impact_summary —— 机器提议阶段（DETECTED/CONFIRMING）不允许被
       ETA 波动带着降档（tests/unit/test_closure_regression.py 的验收口径），
       自愈只让"按现状计"的那一个因子跟随现实。

    提交方式：本函数只 `save()`（flush），**不自己 commit** —— 请求级事务由
    `app.db.session.get_db` 在请求正常结束时统一 commit（GET 里的自愈因此也能落库），
    调用方（create / confirm / patch 等写接口）的原子性也不会被打断；
    脱离请求上下文（脚本）时由调用方自备 session_scope。
    """
    if str(case.type) != str(ExceptionType.VEHICLE_BREAKDOWN):
        return False
    if str(case.status) in ENDED_EXCEPTION_STATUSES:
        return False
    if order is None:
        order = repos.orders.get(case.order_id)
    if order is None:
        return False

    repairing = vehicle_is_repairing(repos, order.vehicle_id)
    factors = [factor for factor in (case.risk_factors_json or []) if isinstance(factor, dict)]
    has_factor = any(str(factor.get("code")) == VEHICLE_BREAKDOWN_FACTOR for factor in factors)
    if repairing == has_factor:
        return False

    if repairing:
        factors = [*factors, risk_rules.vehicle_breakdown_factor().as_dict()]
    else:
        factors = [f for f in factors if str(f.get("code")) != VEHICLE_BREAKDOWN_FACTOR]

    score = min(risk_rules.MAX_SCORE, sum(int(factor.get("weight") or 0) for factor in factors))
    case.risk_factors_json = factors
    case.risk_score = score
    case.level = str(risk_rules.level_of(score))
    bump_version(case)
    repos.exceptions.save(case)
    return True


__all__ = [
    "arrival_reached",
    "match_sla",
    "progress_ratio",
    "recalc_order_eta",
    "refresh_case_impact",
    "resolve_repair_recovery",
    "resolve_promised_at",
    "summary_text",
    "sync_case_vehicle_factor",
    "vehicle_is_repairing",
]
