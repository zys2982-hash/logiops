"""异常检测编排（基线文档 §8.4）：命中 → 建单；已有未关闭异常 → 合并刷新；去抖。

- 机器提议、人确认：自动建单一律停在 DETECTED。
- 同一订单至多一个未关闭异常，再次命中只合并（merged_count+1 + 一条 exception_event）。
- 去抖窗口内只刷新字段，不再写事件、不再累计 merged_count。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core.config import get_settings
from app.core.errors import validation_error
from app.models.enums import (
    ActorType,
    AuditSource,
    ExceptionLevel,
    ExceptionStatus,
    ExceptionType,
)
from app.models.exception import ExceptionCase, freeze_initial_risk
from app.models.transport import Order
from app.repositories import Repos
from app.rules import detection as detection_rules
from app.services import eta_flow, read_models
from app.services.common import add_event, next_case_no, now_naive, to_naive_utc, write_audit


def create_case_record(
    repos: Repos,
    order: Order,
    *,
    exception_type: str,
    occurred_at: datetime,
    detection_rule: str | None,
    detected_by: str = "SYSTEM",
    stall_since: datetime | None = None,
    moment: datetime | None = None,
    actor_id: int | None = None,
) -> ExceptionCase:
    """建异常主单（不含事件/审计，由调用方补齐动作语义）。"""
    now = to_naive_utc(moment) or now_naive()
    match, promised = eta_flow.resolve_promised_at(repos, order)
    case = ExceptionCase(
        workspace_id=int(repos.workspace_id or 0),
        case_no=next_case_no(repos, moment=now),
        order_id=order.id,
        customer_id=order.customer_id,
        vehicle_id=order.vehicle_id,
        carrier_id=order.carrier_id,
        type=str(exception_type),
        level=str(ExceptionLevel.LOW),
        status=str(ExceptionStatus.DETECTED),
        detected_by=detected_by,
        detection_rule=detection_rule,
        occurred_at=to_naive_utc(occurred_at) or now,
        stall_since=to_naive_utc(stall_since),
        promised_delivery_at=promised,
        expected_eta_at=to_naive_utc(order.current_eta_at),
        merged_count=0,
        created_by=actor_id,
    )
    repos.exceptions.add(case)
    eta_flow.refresh_case_impact(repos, case, order, eta_at=case.expected_eta_at)
    # 建单快照（口径 2026-10-08）：风险值是在插入之后才由上面这行写入的，
    # 所以必须在这里再冻结一次 —— initial_* 只在为空时写，之后任何重算都不碰。
    freeze_initial_risk(case)
    return case


def _last_detected_at(repos: Repos, case: ExceptionCase) -> datetime | None:
    events, _ = repos.exception_events.list_for_case(case.id, page=1, page_size=500)
    detected = [event for event in events if str(event.event_type) == "DETECTED"]
    return detected[-1].occurred_at if detected else None


def _merge(
    repos: Repos,
    case: ExceptionCase,
    order: Order,
    decision: detection_rules.DetectionDecision,
    *,
    moment: datetime,
    actor_id: int | None,
) -> bool:
    """合并刷新；返回是否写了事件（去抖窗口内不写）。"""
    settings = get_settings()
    debounced = detection_rules.is_debounced(
        now=moment,
        last_detected_at=_last_detected_at(repos, case),
        debounce_minutes=settings.detect_debounce_minutes,
    )
    # 机器提议阶段（DETECTED/CONFIRMING）不允许静默降档；PROCESSING 允许降级后立即 RESOLVED 收口
    eta_flow.refresh_case_impact(
        repos,
        case,
        order,
        eta_at=order.current_eta_at,
        allow_downgrade=str(case.status) == str(ExceptionStatus.PROCESSING),
    )
    if not debounced:
        case.merged_count = int(case.merged_count or 0) + 1
        add_event(
            repos.session,
            repos,
            exception_id=case.id,
            event_type="DETECTED",
            from_status=case.status,
            to_status=case.status,
            actor_type=ActorType.SYSTEM,
            actor_id=actor_id,
            note=f"检测规则再次命中并合并：{decision.reason}",
            detail={"merged": True, "rule": decision.rule, "stall_minutes": decision.stall_minutes},
        )
    else:
        add_event(
            repos.session,
            repos,
            exception_id=case.id,
            event_type="COMMENT",
            from_status=case.status,
            to_status=case.status,
            actor_type=ActorType.SYSTEM,
            actor_id=actor_id,
            note=f"去抖窗口内仅刷新：{decision.reason}",
            detail={"debounced": True, "rule": decision.rule},
        )
    repos.exceptions.save(case)
    write_audit(
        repos.session,
        repos,
        "exception.merged",
        resource_type="exception",
        resource_id=case.id,
        actor_type=ActorType.SYSTEM,
        after={
            "rule": decision.rule,
            "merged_count": case.merged_count,
            "debounced": debounced,
            "level": case.level,
            "risk_score": case.risk_score,
        },
        source=AuditSource.SYSTEM,
    )
    return not debounced


def detect_for_order(
    repos: Repos,
    order: Order,
    *,
    moment: datetime | None = None,
    actor_id: int | None = None,
) -> ExceptionCase | None:
    """按检测规则判定是否需要建单/合并；返回受影响的异常单或 None。

    2026-10-05 起只剩"停滞 → 疑似车辆故障"一条（在途不再按预测 ETA 建延误单）。
    """
    if order is None:
        return None
    settings = get_settings()
    now = to_naive_utc(moment) or now_naive()
    existing = repos.exceptions.find_open_by_order(order.id)
    last_move = read_models.last_move_at(repos, order.id)

    decision = detection_rules.decide(
        now=now,
        order_status=order.status,
        last_move_at=last_move,
        stall_threshold_minutes=settings.detect_stall_minutes,
        has_open_exception=existing is not None,
    )
    if not decision.rule:
        return None

    if decision.should_create:
        case = create_case_record(
            repos,
            order,
            exception_type=str(decision.exception_type),
            occurred_at=now,
            detection_rule=decision.rule,
            detected_by="SYSTEM",
            stall_since=last_move,
            moment=now,
            actor_id=actor_id,
        )
        add_event(
            repos.session,
            repos,
            exception_id=case.id,
            event_type="DETECTED",
            from_status=None,
            to_status=case.status,
            actor_type=ActorType.SYSTEM,
            actor_id=actor_id,
            note=decision.reason,
            detail=decision.as_dict(),
        )
        write_audit(
            repos.session,
            repos,
            "exception.detected",
            resource_type="exception",
            resource_id=case.id,
            actor_type=ActorType.SYSTEM,
            after={
                "case_no": case.case_no,
                "rule": decision.rule,
                "type": case.type,
                "level": case.level,
                "risk_score": case.risk_score,
                "sla_breached": case.sla_breached,
            },
            source=AuditSource.SYSTEM,
        )
        return case

    if decision.merge_only and existing is not None:
        _merge(repos, existing, order, decision, moment=now, actor_id=actor_id)
        return existing
    return None


def evaluate_detection(
    repos: Repos,
    order: Order,
    *,
    moment: datetime | None = None,
) -> detection_rules.DetectionDecision:
    """只做判定不落库（供单测/演示展示"规则真的在算"）。

    2026-10-05 起在途只判定停滞（延误在送达时按实际时间判定，不看预测 ETA）。
    """
    settings = get_settings()
    now = to_naive_utc(moment) or now_naive()
    existing = repos.exceptions.find_open_by_order(order.id)
    return detection_rules.decide(
        now=now,
        order_status=order.status,
        last_move_at=read_models.last_move_at(repos, order.id),
        stall_threshold_minutes=settings.detect_stall_minutes,
        has_open_exception=existing is not None,
    )


def validate_exception_type(value: Any) -> str:
    text = str(value or "").upper()
    if text not in {member.value for member in ExceptionType}:
        allowed = [member.value for member in ExceptionType]
        raise validation_error("异常类型非法", fields=[{"loc": "type", "msg": f"允许值 {allowed}"}])
    return text


__all__ = [
    "create_case_record",
    "detect_for_order",
    "evaluate_detection",
    "validate_exception_type",
]
