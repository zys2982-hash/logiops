"""脚本化案例与异常数据（§13.3）。

纪律：**所有状态/等级/ETA/违约都必须由 app.rules 真算**（``detection.decide`` / ``sla.match_rule`` /
``sla.compute_promised_at`` / ``sla.evaluate`` / ``eta.recalc`` / ``risk.evaluate_risk``），
seed 只提供"事实"（订单、轨迹、承运商原文），不硬编码 level / sla_breached / risk_score。

案例清单：
- CASE-A 主案例 ``SO20260930021``：天津→上海、VIP-01、津A·12345 济南 REPAIRING、停滞 180min
- CASE-B 已闭环：分析 + 审批 + 通知 + 跟进 + 关闭 + 审计全留痕
- CASE-C 误报：静止 130min，人工判定 INVALID 关闭
- CASE-D 边界：延误 25min ≤ 允许 30min → 不违约（另附 D2 = 31min 违约，展示边界两侧）
- CASE-E 多单并存：5 单覆盖 CRITICAL/HIGH/MEDIUM
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from app.core.clock import to_local
from app.models.ai import AiAnalysis, AiAnalysisStep, Approval
from app.models.enums import (
    ApprovalAction,
    ApprovalStatus,
    DetectionRule,
    ExceptionEventType,
    ExceptionStatus,
    ExceptionType,
    FollowupSource,
    FollowupStatus,
    MessageChannel,
    NotificationChannel,
    NotificationStatus,
    OrderStatus,
    ParseStatus,
    StepStatus,
    TrackingEventType,
)
from app.models.exception import CarrierMessage, ExceptionCase, ExceptionEvent, FollowupTask, Notification
from app.models.master import Customer
from app.models.ops import AuditLog
from app.rules import detection
from app.rules import eta as eta_rules
from app.rules import risk as risk_rules
from app.rules import sla as sla_rules
from app.seed import catalog
from app.seed.catalog import EVENT_FRACTIONS
from app.seed.state import SeedContext

STALL_THRESHOLD_MINUTES = 120
# 通用异常条数：脚本化案例（A/B/C/D2/E×5 = 9）+ 通用池 = 50。
# 2026-10-05 新模型下 CASE-D1（送达未违约）**不再建单**（负样本），所以通用池 +1 保持总数 50。
GENERIC_CASE_TOTAL = 41
CASE_A_RAW_TEXT = "车在济南爆胎了，现在联系修理厂，预计晚上 8 点恢复。"
CASE_A_RECOVERY_LOCAL_HOUR = 20

# 通用异常"证据带"：固定 客户等级 × 类型 × 延误分钟 → 期望等级（等级仍由规则算出，这里只做自检）
#
# 2026-10-05 新风险模型：车辆故障单 = 车辆故障 1 + 客户等级；延误单 = 延误档位 1/2/3 + 客户等级
# （建单前提已是违约，不再加"违约 1"）。所以期望等级按新公式重算：
#   车辆单 → 1 + (NORM 0 / VIP 1 / SVIP 2) = 1~3；延误单 → 档位 + 客户等级（封顶 4）
GENERIC_BANDS: list[dict[str, Any]] = [
    {"level": "MEDIUM", "code": "NORM-01", "case_type": ExceptionType.VEHICLE_BREAKDOWN, "delay": 0},
    {"level": "MEDIUM", "code": "NORM-02", "case_type": ExceptionType.VEHICLE_BREAKDOWN, "delay": 0},
    {"level": "MEDIUM", "code": "NORM-03", "case_type": ExceptionType.DELAY_RISK, "delay": 45},
    {"level": "MEDIUM", "code": "NORM-04", "case_type": ExceptionType.DELAY_RISK, "delay": 200},
    {"level": "MEDIUM", "code": "NORM-05", "case_type": ExceptionType.VEHICLE_BREAKDOWN, "delay": 0},
    {"level": "HIGH", "code": "NORM-06", "case_type": ExceptionType.DELAY_RISK, "delay": 400},
    {"level": "MEDIUM", "code": "VIP-02", "case_type": ExceptionType.VEHICLE_BREAKDOWN, "delay": 0},
    {"level": "CRITICAL", "code": "VIP-03", "case_type": ExceptionType.DELAY_RISK, "delay": 400},
    {"level": "HIGH", "code": "SVIP-01", "case_type": ExceptionType.DELAY_RISK, "delay": 45},
    {"level": "CRITICAL", "code": "VIP-01", "case_type": ExceptionType.DELAY_RISK, "delay": 400},
]
GENERIC_STATUS_CYCLE: list[str] = [
    # 4 状态模型：待确认 → 处理中 → 已解决 → 已关闭（不再有 CONFIRMING / ANALYZING）
    str(ExceptionStatus.DETECTED),
    str(ExceptionStatus.PROCESSING),
    str(ExceptionStatus.RESOLVED),
    str(ExceptionStatus.CLOSED),
    str(ExceptionStatus.PROCESSING),
    str(ExceptionStatus.RESOLVED),
    str(ExceptionStatus.CLOSED),
    str(ExceptionStatus.DETECTED),
]
OPEN_CASE_STATUSES = {
    str(ExceptionStatus.DETECTED),
    str(ExceptionStatus.PROCESSING),
}


# --- 公共小工具 ---------------------------------------------------------------
def _local_day(now: datetime, hour: int, minute: int = 0) -> datetime:
    """业务基准日内的本地时刻（返回朴素 UTC）。"""
    local = to_local(now).replace(hour=hour, minute=minute, second=0, microsecond=0)
    return local.astimezone(UTC).replace(tzinfo=None)


def _customer(ctx: SeedContext, code: str) -> Customer:
    return next(customer for customer in ctx.customers if customer.code == code)


def rule_facts(
    ctx: SeedContext,
    *,
    customer: Customer,
    order: Any,
    delay_minutes: int,
    case_type: str,
    expected_eta_at: datetime | None = None,
    promised_override: datetime | None = None,
) -> tuple[Any, datetime, datetime, Any, Any]:
    """SLA 匹配 → 承诺时刻 → 违约判定 → 风险等级，全部走规则（§8.3 / §8.6）。"""
    match = sla_rules.match_rule(
        ctx.sla_rules, customer_code=customer.code, customer_level=customer.level
    )
    promised = (
        promised_override
        or order.promised_delivery_at
        or sla_rules.compute_promised_at(order.dispatched_at, match.deadline_offset_hours)
    )
    expected = expected_eta_at or (promised + timedelta(minutes=delay_minutes))
    impact = sla_rules.evaluate(match, promised_delivery_at=promised, expected_eta_at=expected)
    risk = risk_rules.evaluate_risk(
        delay_minutes=impact.delay_minutes,
        customer_level=customer.level,
        exception_type=case_type,
    )
    return match, promised, expected, impact, risk


def _impact_summary(order: Any, customer: Customer, impact: Any, expected: datetime) -> str:
    flag = "已违约" if impact.breached else "未违约"
    local = to_local(expected).strftime("%m-%d %H:%M")
    return (
        f"{customer.name}（{customer.level}）SO{order.order_no[2:]} 预计 {local} 到达，"
        f"延误 {impact.delay_minutes} 分钟，SLA {flag}"
    )


def add_event(
    ctx: SeedContext,
    case: ExceptionCase,
    event_type: str,
    *,
    actor_type: str = "SYSTEM",
    actor_id: int | None = None,
    from_status: str | None = None,
    to_status: str | None = None,
    note: str | None = None,
    detail: dict[str, Any] | None = None,
    occurred_at: datetime | None = None,
) -> ExceptionEvent:
    event = ExceptionEvent(
        workspace_id=ctx.workspace_id,
        exception_id=case.id,
        event_type=str(event_type),
        actor_type=actor_type,
        actor_id=actor_id,
        from_status=from_status,
        to_status=to_status,
        detail_json=detail,
        note=note,
        occurred_at=occurred_at or case.created_at,
    )
    ctx.session.add(event)
    ctx.bump("exception_events")
    return event


def add_audit(
    ctx: SeedContext,
    action: str,
    *,
    resource_type: str | None = None,
    resource_id: int | None = None,
    actor_type: str = "SYSTEM",
    actor_id: int | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    source: str = "SYSTEM",
    occurred_at: datetime | None = None,
) -> AuditLog:
    log = AuditLog(
        workspace_id=ctx.workspace_id,
        actor_type=actor_type,
        actor_id=actor_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        before_json=before,
        after_json=after,
        source=source,
        occurred_at=occurred_at or ctx.now,
    )
    ctx.session.add(log)
    ctx.bump("audit_logs")
    return log


def build_exception(
    ctx: SeedContext,
    *,
    order: Any,
    customer: Customer,
    case_type: str,
    status: str,
    occurred_at: datetime,
    detection_rule: str | None,
    delay_minutes: int = 0,
    expected_eta_at: datetime | None = None,
    promised_override: datetime | None = None,
    detected_by: str = "SYSTEM",
    stall_since: datetime | None = None,
    created_at: datetime | None = None,
    assigned_to: int | None = None,
    close_reason: str | None = None,
    resolved_at: datetime | None = None,
    closed_at: datetime | None = None,
    root_cause_code: str | None = None,
    root_cause_note: str | None = None,
    merged_count: int = 0,
) -> ExceptionCase:
    _match, promised, expected, impact, risk = rule_facts(
        ctx,
        customer=customer,
        order=order,
        delay_minutes=delay_minutes,
        case_type=case_type,
        expected_eta_at=expected_eta_at,
        promised_override=promised_override,
    )
    stamp = created_at or occurred_at
    # 车辆故障单不做 SLA 判定（用户口径 2026-10-05：普通的车辆异常订单不应该有 SLA 影响）；
    # 延误单才有延误/SLA 字段（且建单前提是"实际送达已超允许延迟"）
    vehicle_case = str(case_type) == str(ExceptionType.VEHICLE_BREAKDOWN)
    impact_summary = (
        f"车辆故障：{root_cause_note or '车辆异常，待人工确认处置'}"
        if vehicle_case
        else _impact_summary(order, customer, impact, expected)
    )
    case = ExceptionCase(
        workspace_id=ctx.workspace_id,
        case_no=ctx.next_case_no(occurred_at),
        order_id=order.id,
        customer_id=customer.id,
        vehicle_id=order.vehicle_id,
        carrier_id=order.carrier_id,
        type=str(case_type),
        level=str(risk.level),
        status=str(status),
        detected_by=detected_by,
        detection_rule=detection_rule,
        occurred_at=occurred_at,
        stall_since=stall_since,
        root_cause_code=root_cause_code,
        root_cause_note=root_cause_note,
        impact_summary=impact_summary,
        promised_delivery_at=promised,
        current_eta_at=order.current_eta_at or promised,
        expected_eta_at=expected,
        sla_delay_minutes=None if vehicle_case else impact.delay_minutes,
        sla_breached=False if vehicle_case else bool(impact.breached),
        risk_score=risk.score,
        risk_factors_json=risk.factor_dicts,
        assigned_to=assigned_to,
        resolved_at=resolved_at,
        closed_at=closed_at,
        close_reason=close_reason,
        merged_count=merged_count,
        created_at=stamp,
        updated_at=closed_at or resolved_at or stamp,
    )
    ctx.session.add(case)
    ctx.session.flush()
    ctx.bump("exceptions")
    return case


# --- CASE-A（主案例） ---------------------------------------------------------
def build_case_a(ctx: SeedContext) -> ExceptionCase:
    order = ctx.order_at(21)
    ctx.used_order_indices.add(21)
    customer = _customer(ctx, "VIP-01")
    operator = ctx.users["OPERATOR"]
    vehicle = next(item for item in ctx.vehicles if item.plate_no == catalog.CASE_A_PLATE)
    now = ctx.now

    match = sla_rules.match_rule(ctx.sla_rules, customer_code=customer.code, customer_level=customer.level)
    recovery_at = _local_day(now, catalog.CASE_A_RECOVERY_LOCAL_HOUR)
    last_move_at = now - timedelta(minutes=catalog.CASE_A_STALL_MINUTES)

    # ETA 由规则算：车辆 REPAIRING + 承运商给出恢复时间 → REPAIR_WAIT/均速/兜底
    eta_result = eta_rules.recalc(
        now=now,
        distance_km=catalog.CASE_A_TOTAL_KM,
        progress_ratio=catalog.CASE_A_TRAVELED_KM / catalog.CASE_A_TOTAL_KM,
        repair_recovery_at=recovery_at,
        events=[],
    )
    expected_eta = eta_result.eta_at.replace(tzinfo=None)

    # 反推发车时间：让"事实"落成目标延误（240–300min），等级/违约仍由规则算
    promised = expected_eta - timedelta(minutes=catalog.CASE_A_TARGET_DELAY_MINUTES)
    dispatched_at = promised - timedelta(hours=match.deadline_offset_hours)

    order.status = str(OrderStatus.IN_TRANSIT)
    order.carrier_id = ctx.carriers[0].id
    order.vehicle_id = vehicle.id
    order.driver_id = vehicle.current_driver_id
    order.distance_km = catalog.CASE_A_TOTAL_KM
    order.sla_rule_id = match.rule_id
    order.dispatched_at = dispatched_at
    order.promised_delivery_at = promised
    order.original_eta_at = promised
    order.current_eta_at = expected_eta
    order.delivered_at = None
    order.updated_at = now
    ctx.session.add(order)

    # 轨迹：最后一条"移动类"事件（非 STOP/NOTE）严格落在业务基准时间前 180 分钟
    timeline = [
        (TrackingEventType.DEPART, "天津", dispatched_at, 62.0),
        (TrackingEventType.ARRIVE, "滨州", dispatched_at + timedelta(hours=2), 68.0),
        (TrackingEventType.STOP, "滨州", dispatched_at + timedelta(hours=2, minutes=30), 0.0),
        (TrackingEventType.RESUME, "滨州", dispatched_at + timedelta(hours=3), 55.0),
        (TrackingEventType.ARRIVE, "济南", last_move_at, 74.0),
        (TrackingEventType.STOP, "济南", last_move_at + timedelta(minutes=15), 0.0),
    ]
    ctx.plans[21]["events"] = [
        {
            "event_type": str(event_type),
            "city": city,
            "occurred_at": occurred_at,
            "speed_kmh": Decimal(str(speed)),
            "source": "DRIVER" if event_type == TrackingEventType.DEPART else "MOCK",
            "payload_json": {"note": "济南爆胎，车辆维修中"} if event_type == TrackingEventType.STOP else None,
        }
        for event_type, city, occurred_at, speed in timeline
    ]
    ctx.plans[21]["last_move_at"] = last_move_at
    ctx.plans[21]["current_city"] = "济南"
    ctx.plans[21]["dispatched_at"] = dispatched_at

    decision = detection.decide(
        now=now,
        order_status=order.status,
        last_move_at=last_move_at,
        stall_threshold_minutes=STALL_THRESHOLD_MINUTES,
        has_open_exception=False,
    )
    if decision.rule != str(DetectionRule.STALL_OVER_THRESHOLD) or not decision.should_create:
        raise RuntimeError(f"CASE-A 未命中 STALL_OVER_THRESHOLD：{decision.as_dict()}")

    created_at = now - timedelta(minutes=60)
    case = build_exception(
        ctx,
        order=order,
        customer=customer,
        case_type=str(ExceptionType.VEHICLE_BREAKDOWN),
        # 4 状态模型：CASE-A 停在「待确认」（机器已建单 + 已录入承运商消息，等人点「确认异常」）
        status=str(ExceptionStatus.DETECTED),
        occurred_at=last_move_at,
        detection_rule=decision.rule,
        expected_eta_at=expected_eta,
        promised_override=promised,
        stall_since=last_move_at,
        created_at=created_at,
        assigned_to=operator.id,
        root_cause_code="VEHICLE_BREAKDOWN",
        root_cause_note="承运商反馈右后轮爆胎，正在联系修理厂",
    )
    # 新风险模型自检（2026-10-05）：车辆故障单 = 车辆故障 1 + VIP 1 = 2 → MEDIUM；
    # 且**不做 SLA 判定**（没有延误/违约因子与字段）
    if case.level != "MEDIUM" or int(case.risk_score or 0) != 2:
        raise RuntimeError(
            f"CASE-A 规则自检失败：level={case.level} score={case.risk_score}（应为 MEDIUM/2）"
        )
    if case.sla_delay_minutes is not None or case.sla_breached:
        raise RuntimeError(
            f"CASE-A 车辆单不应有 SLA 判定：delay={case.sla_delay_minutes} breached={case.sla_breached}"
        )
    factor_codes = [str(f.get("code")) for f in (case.risk_factors_json or [])]
    if sorted(factor_codes) != ["CUSTOMER_VIP", "VEHICLE_BREAKDOWN"]:
        raise RuntimeError(f"CASE-A 因子应为 车辆故障 + VIP：{factor_codes}")
    ctx.counts["case_a_risk_score"] = int(case.risk_score or 0)
    ctx.counts["case_a_level"] = case.level
    ctx.counts["case_a_stall_minutes"] = catalog.CASE_A_STALL_MINUTES
    ctx.counts["case_a_eta_method"] = eta_result.method
    ctx.counts["case_a_eta_detail"] = eta_result.detail
    ctx.counts["case_a_sla_rule"] = (
        f"{match.rule_name}（{match.scope_type}:{match.scope_value}，"
        f"{match.deadline_offset_hours}h/{match.max_delay_minutes}min）"
    )

    add_event(
        ctx,
        case,
        ExceptionEventType.DETECTED,
        detail={"detection_rule": decision.rule, "stall_minutes": decision.stall_minutes, "reason": decision.reason},
        occurred_at=created_at,
    )
    message = CarrierMessage(
        workspace_id=ctx.workspace_id,
        exception_id=case.id,
        order_id=order.id,
        channel=str(MessageChannel.MANUAL_PASTE),
        sender_name="赵队长",
        sender_role="CARRIER",
        raw_text=CASE_A_RAW_TEXT,
        received_at=created_at + timedelta(minutes=10),
        parse_status=str(ParseStatus.PARSED),
        parse_result_json={
            "exception_type": str(ExceptionType.VEHICLE_BREAKDOWN),
            "location": "济南",
            "status": "REPAIRING",
            # 业务基准日内 20:00+08（承运商原文"预计晚上 8 点恢复"的解析结果）
            "estimated_recovery_at": to_local(recovery_at).isoformat(),
            "confidence": 0.93,
            "missing_info": [],
        },
        parser_version="seed-v1",
        created_by=operator.id,
        created_at=created_at + timedelta(minutes=10),
    )
    ctx.session.add(message)
    ctx.bump("carrier_messages")
    add_event(
        ctx,
        case,
        ExceptionEventType.MESSAGE_ADDED,
        actor_type="USER",
        actor_id=operator.id,
        from_status=str(ExceptionStatus.DETECTED),
        to_status=str(ExceptionStatus.DETECTED),
        note="录入承运商消息：济南爆胎，预计 20:00 恢复（等人工确认）",
        occurred_at=created_at + timedelta(minutes=10),
    )
    add_audit(
        ctx,
        "exception.detected",
        resource_type="exception_case",
        resource_id=case.id,
        after={"case_no": case.case_no, "rule": decision.rule, "level": case.level},
        source="SYSTEM",
        occurred_at=created_at,
    )
    add_audit(
        ctx,
        "exception.message_added",
        resource_type="carrier_message",
        resource_id=message.id,
        actor_type="USER",
        actor_id=operator.id,
        after={"channel": str(MessageChannel.MANUAL_PASTE), "parse_status": str(ParseStatus.PARSED)},
        source="MANUAL",
        occurred_at=created_at + timedelta(minutes=10),
    )
    return case


# --- CASE-B（已闭环，全留痕） -------------------------------------------------
def build_case_b(ctx: SeedContext) -> ExceptionCase:
    order = ctx.order_at(22)
    ctx.used_order_indices.add(22)
    customer = _customer(ctx, "VIP-02")
    operator = ctx.users["OPERATOR"]
    now = ctx.now

    match = sla_rules.match_rule(ctx.sla_rules, customer_code=customer.code, customer_level=customer.level)
    promised = order.promised_delivery_at or sla_rules.compute_promised_at(
        order.dispatched_at, match.deadline_offset_hours
    )
    delivered_at = promised - timedelta(minutes=30)
    order.status = str(OrderStatus.DELIVERED)
    order.delivered_at = delivered_at
    order.current_eta_at = promised
    order.updated_at = now
    ctx.session.add(order)

    occurred_at = (order.dispatched_at or now - timedelta(hours=30)) + timedelta(hours=6)
    created_at = occurred_at + timedelta(minutes=5)
    resolved_at = delivered_at
    closed_at = delivered_at + timedelta(hours=24)

    case = build_exception(
        ctx,
        order=order,
        customer=customer,
        case_type=str(ExceptionType.VEHICLE_BREAKDOWN),
        status=str(ExceptionStatus.CLOSED),
        occurred_at=occurred_at,
        detection_rule=str(DetectionRule.STALL_OVER_THRESHOLD),
        delay_minutes=0,
        promised_override=promised,
        detected_by="SYSTEM",
        stall_since=occurred_at - timedelta(minutes=135),
        created_at=created_at,
        assigned_to=operator.id,
        close_reason="DELIVERED",
        resolved_at=resolved_at,
        closed_at=closed_at,
        root_cause_code="VEHICLE_BREAKDOWN",
        root_cause_note="轮胎故障，现场更换后恢复行驶",
    )
    ctx.counts["case_b_level"] = case.level

    human_events = {ExceptionEventType.CONFIRMED, ExceptionEventType.APPROVED}
    for event_type, note, minutes in [
        (ExceptionEventType.DETECTED, "停滞 135 分钟，规则自动建单", 0),
        (ExceptionEventType.CONFIRMED, "已联系承运商确认故障", 10),
        (ExceptionEventType.ANALYSIS_REQUESTED, "触发 AI 分析", 15),
        (ExceptionEventType.ANALYSIS_READY, "AI 分析完成，生成 1 条建议", 16),
        (ExceptionEventType.APPROVED, "人工批准 ETA 更新建议", 25),
        (ExceptionEventType.EXECUTED, "执行器更新订单 ETA", 26),
        (ExceptionEventType.FOLLOWUP_DONE, "回访承运商确认已恢复", 40),
        (ExceptionEventType.CLOSED, "订单送达后自动关闭", 1440),
    ]:
        add_event(
            ctx,
            case,
            event_type,
            actor_type="USER" if event_type in human_events else "SYSTEM",
            actor_id=operator.id if event_type in human_events else None,
            note=note,
            occurred_at=created_at + timedelta(minutes=minutes),
        )

    analysis = AiAnalysis(
        workspace_id=ctx.workspace_id,
        exception_id=case.id,
        analysis_no=f"AN{to_local(created_at.replace(tzinfo=UTC)).strftime('%Y%m%d')}B001",
        task_type="ANALYZE_EXCEPTION",
        status="READY",
        triggered_by=operator.id,
        input_hash=hashlib.sha256(f"{case.case_no}|CASE-B".encode()).hexdigest(),
        model="deepseek-chat",
        prompt_version="v1",
        output_json={
            "summary": f"{customer.name}车辆轮胎故障，已现场修复，预计按承诺时间到达。",
            "root_cause": {"code": "VEHICLE_BREAKDOWN", "note": "右后轮爆胎，现场更换备胎"},
            "impact": {
                "delay_minutes": int(case.sla_delay_minutes or 0),
                "sla_breached": bool(case.sla_breached),
                "affected_customer_level": customer.level,
            },
            "suggestions": [
                {"code": "UPDATE_ETA", "title": "按恢复情况更新 ETA", "rationale": "维修完成后按剩余里程重算"},
                {"code": "CREATE_FOLLOWUP", "title": "到货后回访承运商", "assignee_role": "OPERATOR"},
            ],
            "open_questions": ["是否需要更换第二条轮胎？"],
            "evidence_refs": [{"type": "TRACKING_EVENT", "id": 1, "note": f"{case.case_no} 停滞 135 分钟"}],
        },
        raw_output='{"summary": "..."}',
        risk_level_calculated=case.level,
        tokens_in=1820,
        tokens_out=436,
        latency_ms=2410,
        is_replay=True,
        started_at=created_at + timedelta(minutes=15),
        finished_at=created_at + timedelta(minutes=16),
        created_at=created_at + timedelta(minutes=15),
    )
    ctx.session.add(analysis)
    ctx.session.flush()
    ctx.bump("ai_analyses")

    step_plan = [
        ("TOOL", "get_order", "SO20260930022 上海→北京 DELIVERED"),
        ("TOOL", "get_tracking_events", "7 条轨迹，最后位置 济南"),
        ("TOOL", "get_customer_sla", "VIP：发车后 24h，允许延迟 0min"),
        ("TOOL", "get_vehicle", "沪B·12347 已恢复行驶"),
        ("TOOL", "get_exception_history", "近 90 天 1 次"),
        ("TOOL", "search_knowledge", "命中 2 条（车辆故障处理规范#2.1）"),
        ("VALIDATE", None, "schema 校验通过"),
    ]
    for index, (step_type, tool_name, summary_text) in enumerate(step_plan, start=1):
        ctx.session.add(
            AiAnalysisStep(
                analysis_id=analysis.id,
                step_no=index,
                step_type=step_type,
                tool_name=tool_name,
                args_json={"exception_id": case.id} if tool_name else None,
                result_summary=summary_text,
                status=str(StepStatus.OK),
                duration_ms=8 + index * 3,
                created_at=created_at + timedelta(minutes=15),
            )
        )
        ctx.bump("ai_analysis_steps")

    approval = Approval(
        workspace_id=ctx.workspace_id,
        exception_id=case.id,
        analysis_id=analysis.id,
        action_type=str(ApprovalAction.UPDATE_ETA),
        target_type="order",
        target_id=order.id,
        ai_payload_json={"eta_at": promised.isoformat(), "reason": "维修完成，按剩余里程重算"},
        final_payload_json={"eta_at": promised.isoformat(), "reason": "人工确认：按承诺时间到达"},
        diff_json={"changed": [], "ai_value": promised.isoformat(), "final_value": promised.isoformat()},
        status=str(ApprovalStatus.EXECUTED),
        decided_by=operator.id,
        decided_at=created_at + timedelta(minutes=25),
        executed_at=created_at + timedelta(minutes=26),
        execution_result_json={"order_id": order.id, "updated_fields": ["current_eta_at"]},
        expires_at=created_at + timedelta(hours=24),
        created_at=created_at + timedelta(minutes=16),
        updated_at=created_at + timedelta(minutes=26),
    )
    ctx.session.add(approval)
    ctx.bump("approvals")

    followup = FollowupTask(
        workspace_id=ctx.workspace_id,
        exception_id=case.id,
        title="到货后回访承运商确认故障是否彻底排除",
        content="确认备胎更换情况，避免二次故障。",
        assignee_user_id=operator.id,
        due_at=created_at + timedelta(minutes=40),
        priority="HIGH",
        status=str(FollowupStatus.DONE),
        source=str(FollowupSource.AI_SUGGESTED),
        source_approval_id=approval.id,
        done_at=created_at + timedelta(minutes=40),
        done_by=operator.id,
        remark="已回访，车辆恢复正常",
        created_at=created_at + timedelta(minutes=16),
    )
    ctx.session.add(followup)
    ctx.bump("followup_tasks")

    notification = Notification(
        workspace_id=ctx.workspace_id,
        exception_id=case.id,
        customer_id=customer.id,
        channel=str(NotificationChannel.MANUAL_COPY),
        subject=f"【{order.order_no}】运输异常已处理完成",
        content=(
            f"尊敬的{customer.name}：贵司订单 {order.order_no}（上海→北京）途中车辆轮胎故障，"
            f"已现场修复，预计按承诺时间 {to_local(promised.replace(tzinfo=UTC)).strftime('%m-%d %H:%M')} 前送达。"
        ),
        ai_draft_content="（AI 草稿）车辆故障已处理，预计按承诺时间送达。",
        status=str(NotificationStatus.SENT_MOCK),
        approved_by=operator.id,
        approved_at=created_at + timedelta(minutes=27),
        sent_at=created_at + timedelta(minutes=28),
        source_approval_id=approval.id,
        created_at=created_at + timedelta(minutes=26),
    )
    ctx.session.add(notification)
    ctx.bump("notifications")

    for action, source, minutes in [
        ("exception.detected", "SYSTEM", 0),
        ("exception.confirm", "MANUAL", 10),
        ("ai_analysis.ready", "SYSTEM", 16),
        ("approval.approve", "MANUAL", 25),
        ("approval.execute", "APPROVED_AI", 26),
        ("followup.done", "MANUAL", 40),
        ("exception.close", "SYSTEM", 1440),
    ]:
        add_audit(
            ctx,
            action,
            resource_type="exception_case",
            resource_id=case.id,
            actor_type="USER" if source == "MANUAL" else "SYSTEM",
            actor_id=operator.id if source == "MANUAL" else None,
            source=source,
            after={"case_no": case.case_no, "status": case.status},
            occurred_at=created_at + timedelta(minutes=minutes),
        )
    return case


# --- CASE-C（误报关闭） -------------------------------------------------------
def build_case_c(ctx: SeedContext) -> ExceptionCase:
    index = 23
    order = ctx.order_at(index)
    ctx.used_order_indices.add(index)
    customer = _customer(ctx, "NORM-02")
    operator = ctx.users["OPERATOR"]
    now = ctx.now

    order.status = str(OrderStatus.IN_TRANSIT)
    order.updated_at = now
    ctx.session.add(order)

    last_move_at = now - timedelta(minutes=130)
    ctx.plans[index]["last_move_at"] = last_move_at
    events = ctx.plans[index]["events"]
    if events:
        events[-1]["occurred_at"] = last_move_at
        events[-1]["city"] = "淄博"
    decision = detection.decide(
        now=now,
        order_status=order.status,
        last_move_at=last_move_at,
        stall_threshold_minutes=STALL_THRESHOLD_MINUTES,
    )
    if decision.rule != str(DetectionRule.STALL_OVER_THRESHOLD):
        raise RuntimeError(f"CASE-C 未命中停滞规则：{decision.as_dict()}")

    occurred_at = last_move_at
    created_at = last_move_at + timedelta(minutes=15)
    closed_at = created_at + timedelta(minutes=50)
    case = build_exception(
        ctx,
        order=order,
        customer=customer,
        case_type=str(ExceptionType.VEHICLE_BREAKDOWN),
        status=str(ExceptionStatus.CLOSED),
        occurred_at=occurred_at,
        detection_rule=decision.rule,
        delay_minutes=0,
        detected_by="SYSTEM",
        stall_since=last_move_at,
        created_at=created_at,
        close_reason="INVALID",
        closed_at=closed_at,
        root_cause_code="UNKNOWN",
        root_cause_note="运营确认：实为装卸排队，并非车辆故障",
    )
    ctx.counts["case_c_level"] = case.level
    add_event(
        ctx,
        case,
        ExceptionEventType.DETECTED,
        detail={"detection_rule": decision.rule, "stall_minutes": decision.stall_minutes},
        occurred_at=created_at,
    )
    add_event(
        ctx,
        case,
        ExceptionEventType.CLOSED,
        actor_type="USER",
        actor_id=operator.id,
        from_status=str(ExceptionStatus.DETECTED),
        to_status=str(ExceptionStatus.CLOSED),
        note="误报：装卸排队，人工关闭（reason=INVALID）",
        occurred_at=closed_at,
    )
    add_audit(
        ctx,
        "exception.close",
        resource_type="exception_case",
        resource_id=case.id,
        actor_type="USER",
        actor_id=operator.id,
        source="MANUAL",
        after={"close_reason": "INVALID", "note": "误报控制：装卸排队"},
        occurred_at=closed_at,
    )
    return case


# --- CASE-D（送达边界：25min 不建单 vs 31min 建单） ---------------------------
def build_case_d(ctx: SeedContext) -> ExceptionCase:
    """新模型（2026-10-05）：延误只在**送达时**用 `实际送达 − 承诺送达` 与允许延迟比对。

    · D1：**25min ≤ 允许 30min → 不产生异常单**（负样本，证明"未违约就不建单"）；
    · D2：**31min > 允许 30min → 自动建延误单**（detection_rule=DELIVERED_BREACH）。
    """
    operator = ctx.users["OPERATOR"]
    now = ctx.now

    # D1：送达未违约 → 没有异常单
    order = ctx.order_at(24)
    ctx.used_order_indices.add(24)
    match = sla_rules.match_rule(ctx.sla_rules, customer_code=_customer(ctx, "NORM-04").code,
                                 customer_level=_customer(ctx, "NORM-04").level)
    d1_promised = order.promised_delivery_at or sla_rules.compute_promised_at(
        order.dispatched_at, match.deadline_offset_hours
    )
    d1_delivered = d1_promised + timedelta(minutes=25)
    order.status = str(OrderStatus.DELIVERED)
    order.delivered_at = d1_delivered
    order.current_eta_at = d1_delivered
    order.updated_at = now
    ctx.session.add(order)
    delay_d1 = int(round((d1_delivered - d1_promised).total_seconds() / 60))
    if delay_d1 > match.max_delay_minutes:
        raise RuntimeError(f"CASE-D1 自检失败：{delay_d1}min 不应超过允许 {match.max_delay_minutes}min")
    ctx.counts["case_d_delay_minutes"] = delay_d1
    ctx.counts["case_d_breached"] = False
    ctx.counts["case_d_has_case"] = False

    # D2：送达超允许延迟 → 建延误单
    d2_order = _first_free_order(ctx, preferred_status=str(OrderStatus.IN_TRANSIT))
    d2_customer = _customer(ctx, "NORM-05")
    d2_match = sla_rules.match_rule(
        ctx.sla_rules, customer_code=d2_customer.code, customer_level=d2_customer.level
    )
    d2_promised = d2_order.promised_delivery_at or sla_rules.compute_promised_at(
        d2_order.dispatched_at, d2_match.deadline_offset_hours
    )
    d2_delivered = d2_promised + timedelta(minutes=31)
    d2_order.status = str(OrderStatus.DELIVERED)
    d2_order.delivered_at = d2_delivered
    d2_order.current_eta_at = d2_delivered
    d2_order.updated_at = now
    ctx.session.add(d2_order)
    created_at = d2_delivered + timedelta(minutes=5)
    case_d2 = build_exception(
        ctx,
        order=d2_order,
        customer=d2_customer,
        case_type=str(ExceptionType.DELAY_RISK),
        status=str(ExceptionStatus.RESOLVED),
        occurred_at=d2_delivered,
        detection_rule=str(DetectionRule.DELIVERED_BREACH),
        delay_minutes=31,
        detected_by="SYSTEM",
        created_at=created_at,
        assigned_to=operator.id,
        resolved_at=created_at + timedelta(minutes=60),
        root_cause_code="TRAFFIC",
        root_cause_note="边界案例：送达超时 31 分钟 > 允许 30 分钟，自动建单",
    )
    if not case_d2.sla_breached:
        raise RuntimeError(f"CASE-D2 边界自检失败：delay={case_d2.sla_delay_minutes} 应当违约")
    if str(case_d2.detection_rule) != str(DetectionRule.DELIVERED_BREACH):
        raise RuntimeError(f"CASE-D2 建单规则应为 DELIVERED_BREACH：{case_d2.detection_rule}")
    ctx.counts["case_d2_delay_minutes"] = int(case_d2.sla_delay_minutes or 0)
    ctx.counts["case_d2_level"] = case_d2.level
    return case_d2


# --- CASE-E（多单并存） -------------------------------------------------------
def build_case_e(ctx: SeedContext) -> list[ExceptionCase]:
    operator = ctx.users["OPERATOR"]
    now = ctx.now
    definitions = [
        (25, "VIP-03", ExceptionType.VEHICLE_BREAKDOWN, 0, ExceptionStatus.PROCESSING, "MEDIUM"),
        (26, "VIP-01", ExceptionType.DELAY_RISK, 400, ExceptionStatus.PROCESSING, "CRITICAL"),
        (27, "NORM-01", ExceptionType.DELAY_RISK, 200, ExceptionStatus.DETECTED, "MEDIUM"),
        (28, "SVIP-01", ExceptionType.DELAY_RISK, 45, ExceptionStatus.RESOLVED, "HIGH"),
        (29, "NORM-03", ExceptionType.VEHICLE_BREAKDOWN, 0, ExceptionStatus.CLOSED, "MEDIUM"),
    ]
    cases: list[ExceptionCase] = []
    for index, code, case_type, delay, status, expected_level in definitions:
        order = ctx.order_at(index)
        ctx.used_order_indices.add(index)
        customer = _customer(ctx, code)
        order.status = ctx.plans[index]["status"]
        order.updated_at = now
        ctx.session.add(order)
        created_at = now - timedelta(minutes=60 + index * 30)
        closed = status == str(ExceptionStatus.CLOSED)
        case = build_exception(
            ctx,
            order=order,
            customer=customer,
            case_type=str(case_type),
            status=str(status),
            occurred_at=created_at - timedelta(minutes=30),
            detection_rule=str(DetectionRule.MANUAL),
            delay_minutes=delay,
            detected_by="OPERATOR" if index == 27 else "SYSTEM",
            created_at=created_at,
            assigned_to=operator.id,
            resolved_at=created_at + timedelta(minutes=45) if status != str(ExceptionStatus.DETECTED) else None,
            closed_at=created_at + timedelta(minutes=120) if closed else None,
            close_reason="DELIVERED" if closed else None,
            root_cause_code=str(case_type),
            root_cause_note="CASE-E：多单并存演示数据",
        )
        if case.level != expected_level:
            raise RuntimeError(f"CASE-E[{index}] 等级自检失败：期望 {expected_level}，规则算出 {case.level}")
        cases.append(case)
    return cases


def _first_free_order(ctx: SeedContext, *, preferred_status: str) -> Any:
    """找一个"未被案例占用、且状态符合"的订单。

    精简规模（compact）只生成脚本化案例需要的那几张订单，序号不连续，
    因此这里按**实际存在的订单号**列举候选；完整规模仍从第 30 单开始找
    （1..29 预留给 CASE-A..E，避免抢走它们的订单）。
    """
    indices = sorted(int(order.order_no[-3:]) for order in ctx.orders if order.order_no[-3:].isdigit())
    start = 30 if any(index >= 30 for index in indices) else 0
    for index in [item for item in indices if item >= start]:
        if index in ctx.used_order_indices:
            continue
        if ctx.plans.get(index, {}).get("status") == preferred_status:
            ctx.used_order_indices.add(index)
            return ctx.order_at(index)
    raise RuntimeError(f"没有可用的 {preferred_status} 订单")


# --- 通用异常（补足 50 个，覆盖各等级/状态 + 近 7 天趋势） --------------------
def build_generic_cases(ctx: SeedContext, count: int = GENERIC_CASE_TOTAL) -> list[ExceptionCase]:
    operator = ctx.users["OPERATOR"]
    now = ctx.now
    in_transit = [
        index
        for index in range(1, len(ctx.orders) + 1)
        if ctx.plans[index]["status"] == str(OrderStatus.IN_TRANSIT) and index not in ctx.used_order_indices
    ]
    delivered = [
        index
        for index in range(1, len(ctx.orders) + 1)
        if ctx.plans[index]["status"] == str(OrderStatus.DELIVERED) and index not in ctx.used_order_indices
    ]
    ctx.rng.shuffle(in_transit)
    ctx.rng.shuffle(delivered)
    pool = {"IN_TRANSIT": in_transit, "DELIVERED": delivered}

    cases: list[ExceptionCase] = []
    levels_seen: set[str] = set()
    statuses_seen: set[str] = set()
    for offset in range(count):
        band = GENERIC_BANDS[offset % len(GENERIC_BANDS)]
        status = GENERIC_STATUS_CYCLE[offset % len(GENERIC_STATUS_CYCLE)]
        customer = _customer(ctx, str(band["code"]))
        want_in_transit = status in OPEN_CASE_STATUSES
        queues = [pool["IN_TRANSIT"]] if want_in_transit else [pool["DELIVERED"], pool["IN_TRANSIT"]]
        index = None
        for queue in queues:
            if queue:
                index = queue.pop()
                break
        if index is None:
            raise RuntimeError("通用异常没有可用的订单")
        ctx.used_order_indices.add(index)

        order = ctx.order_at(index)
        # 6 天以内：保证全部落在 Dashboard"近 7 天趋势"窗口内（§10.3）
        created_at = now - timedelta(minutes=ctx.rng.randrange(120, 6 * 24 * 60))
        last_move_at = ctx.plans[index]["last_move_at"] or created_at
        if order.status == str(OrderStatus.IN_TRANSIT):
            stall_minutes = 130 if band["case_type"] == str(ExceptionType.VEHICLE_BREAKDOWN) else 10
            last_move_at = created_at - timedelta(minutes=stall_minutes)
            _reset_timeline(ctx, index, last_move_at)

        match = sla_rules.match_rule(
            ctx.sla_rules, customer_code=customer.code, customer_level=customer.level
        )
        promised = order.promised_delivery_at or sla_rules.compute_promised_at(
            order.dispatched_at, match.deadline_offset_hours
        )
        expected = promised + timedelta(minutes=int(band["delay"]))
        decision = detection.decide(
            now=created_at,
            order_status=order.status,
            last_move_at=last_move_at,
            stall_threshold_minutes=STALL_THRESHOLD_MINUTES,
            has_open_exception=False,
        )
        detection_rule = decision.rule or str(DetectionRule.MANUAL)
        resolved_at = created_at + timedelta(hours=2) if status in {
            str(ExceptionStatus.RESOLVED),
            str(ExceptionStatus.CLOSED),
        } else None
        closed_at = created_at + timedelta(hours=4) if status == str(ExceptionStatus.CLOSED) else None

        case = build_exception(
            ctx,
            order=order,
            customer=customer,
            case_type=str(band["case_type"]),
            status=status,
            occurred_at=created_at - timedelta(minutes=30),
            detection_rule=detection_rule,
            delay_minutes=int(band["delay"]),
            expected_eta_at=expected,
            promised_override=promised,
            detected_by="SYSTEM" if decision.rule else "OPERATOR",
            stall_since=last_move_at if detection_rule == str(DetectionRule.STALL_OVER_THRESHOLD) else None,
            created_at=created_at,
            assigned_to=operator.id if status != str(ExceptionStatus.DETECTED) else None,
            resolved_at=resolved_at,
            closed_at=closed_at,
            close_reason="DELIVERED" if closed_at else None,
            root_cause_code=str(band["case_type"]),
            root_cause_note=f"通用演示数据：{band['case_type']}",
        )
        if case.level != band["level"]:
            raise RuntimeError(
                f"通用异常证据带自检失败：期望 {band['level']}，规则算出 {case.level}"
                f"（delay={case.sla_delay_minutes} breached={case.sla_breached}"
                f" type={case.type} level={customer.level}）"
            )
        levels_seen.add(str(case.level))
        statuses_seen.add(str(case.status))
        add_event(
            ctx,
            case,
            ExceptionEventType.DETECTED,
            detail={"detection_rule": detection_rule, "reason": decision.reason},
            occurred_at=case.created_at,
        )
        if status == str(ExceptionStatus.CLOSED):
            add_event(
                ctx,
                case,
                ExceptionEventType.CLOSED,
                note="演示数据：已闭环",
                occurred_at=closed_at,
            )
        cases.append(case)

    # 新模型下未结束的异常最低是 MEDIUM（车辆故障 1 分起），LOW 只会出现在"已结束"的当前风险里
    if not {"MEDIUM", "HIGH", "CRITICAL"} <= levels_seen:
        raise RuntimeError(f"通用异常未覆盖关键等级：{sorted(levels_seen)}")
    if not {str(ExceptionStatus.DETECTED), str(ExceptionStatus.PROCESSING)} <= statuses_seen:
        raise RuntimeError(f"通用异常未覆盖关键状态：{sorted(statuses_seen)}")
    return cases


def _reset_timeline(ctx: SeedContext, index: int, last_move_at: datetime) -> None:
    """把订单轨迹时间线等比压缩到 [发车, last_move]，让"最后一条移动类轨迹"精确落点。

    检测规则（STALL_OVER_THRESHOLD）就是读这条事件的时间（read_models.last_move_at）。
    """
    plan = ctx.plans[index]
    plan["last_move_at"] = last_move_at
    events = plan["events"]
    dispatched_at = plan["dispatched_at"]
    if not events or dispatched_at is None:
        return
    span_minutes = max((last_move_at - dispatched_at).total_seconds() / 60.0, 1.0)
    for position, spec in enumerate(events):
        fraction = EVENT_FRACTIONS[position] if position < len(EVENT_FRACTIONS) else 1.0
        spec["occurred_at"] = dispatched_at + timedelta(minutes=round(span_minutes * fraction))
    events[-1]["occurred_at"] = last_move_at


def build_all_cases(ctx: SeedContext) -> None:
    """按脚本化案例 → 通用异常的顺序生成 50 个异常（顺序决定 case_no 序号）。"""
    build_case_a(ctx)
    build_case_b(ctx)
    build_case_c(ctx)
    build_case_d(ctx)
    build_case_e(ctx)
    build_generic_cases(ctx)


__all__ = [
    "CASE_A_RAW_TEXT",
    "GENERIC_BANDS",
    "add_audit",
    "add_event",
    "build_all_cases",
    "build_case_a",
    "build_case_b",
    "build_case_c",
    "build_case_d",
    "build_case_e",
    "build_exception",
    "build_generic_cases",
    "rule_facts",
]
