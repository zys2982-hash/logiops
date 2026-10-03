"""订单与异常的状态机（基线文档 §8.1 / §8.2）。

唯一允许改状态的入口：**仅限 ORDER / EXCEPTION 两个业务实体**，任何直接给这两个
status 赋值的代码都算 bug。approval / ai_analysis / notification / followup / vehicle /
driver 的 status 是任务级枚举、没有流转表，由各自服务按业务规则直接赋值（§8.4）。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.core.errors import AppError, ErrorCode
from app.models.enums import ExceptionStatus, OrderStatus


class EntityKind(StrEnum):
    ORDER = "ORDER"
    EXCEPTION = "EXCEPTION"


ORDER_TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.CREATED: {OrderStatus.DISPATCHED, OrderStatus.CANCELLED},
    OrderStatus.DISPATCHED: {OrderStatus.IN_TRANSIT, OrderStatus.CANCELLED},
    OrderStatus.IN_TRANSIT: {OrderStatus.DELIVERED},
    OrderStatus.DELIVERED: {OrderStatus.CLOSED},
    OrderStatus.CLOSED: set(),
    OrderStatus.CANCELLED: set(),
}

EXCEPTION_TRANSITIONS: dict[ExceptionStatus, set[ExceptionStatus]] = {
    ExceptionStatus.DETECTED: {ExceptionStatus.CONFIRMING, ExceptionStatus.CLOSED},
    ExceptionStatus.CONFIRMING: {ExceptionStatus.ANALYZING, ExceptionStatus.CLOSED},
    ExceptionStatus.ANALYZING: {ExceptionStatus.PROCESSING, ExceptionStatus.CONFIRMING, ExceptionStatus.CLOSED},
    ExceptionStatus.PROCESSING: {ExceptionStatus.RESOLVED, ExceptionStatus.CLOSED, ExceptionStatus.ANALYZING},
    # ↑ PROCESSING → ANALYZING = "重新分析"：处理中的单子允许操作者再跑一次分析
    #   （新证据/承运商更新恢复时间/原结论存疑时很常见）；不放开的话，
    #   任何"已进入处理中"的单子都会变成不能再分析的死胡同。
    ExceptionStatus.RESOLVED: {ExceptionStatus.CLOSED},
    ExceptionStatus.CLOSED: set(),
}

TERMINAL_EXCEPTION_STATUSES = {ExceptionStatus.CLOSED}
TERMINAL_ORDER_STATUSES = {OrderStatus.CLOSED, OrderStatus.CANCELLED}

_TRANSITIONS: dict[str, dict[str, set[str]]] = {
    EntityKind.ORDER: ORDER_TRANSITIONS,
    EntityKind.EXCEPTION: EXCEPTION_TRANSITIONS,
}


@dataclass(frozen=True)
class TransitionPlan:
    kind: str
    from_status: str
    to_status: str
    requires_reason: bool


def allowed_targets(kind: EntityKind | str, current: str) -> set[str]:
    table = _TRANSITIONS[str(kind)]
    return table.get(current, set())


def can_transition(kind: EntityKind | str, current: str, target: str) -> bool:
    return target in allowed_targets(kind, current)


def is_terminal(kind: EntityKind | str, status: str) -> bool:
    if str(kind) == str(EntityKind.ORDER):
        return status in TERMINAL_ORDER_STATUSES
    return status in TERMINAL_EXCEPTION_STATUSES


def plan_transition(kind: EntityKind | str, current: str, target: str, reason: str | None = None) -> TransitionPlan:
    """校验流转是否合法，非法时抛 409 STATE_TRANSITION_INVALID。"""
    if current == target:
        raise AppError(
            ErrorCode.STATE_TRANSITION_INVALID,
            f"状态已经是 {target}",
            {"kind": str(kind), "from": current, "to": target},
        )
    if not can_transition(kind, current, target):
        raise AppError(
            ErrorCode.STATE_TRANSITION_INVALID,
            f"不允许的状态流转：{current} → {target}",
            {"kind": str(kind), "from": current, "to": target, "allowed": sorted(allowed_targets(kind, current))},
        )
    requires_reason = target in {ExceptionStatus.CLOSED, OrderStatus.CANCELLED}
    if requires_reason and not reason:
        raise AppError(
            ErrorCode.STATE_TRANSITION_INVALID,
            "关闭/取消类流转必须提供原因",
            {"kind": str(kind), "from": current, "to": target},
        )
    return TransitionPlan(
        kind=str(kind), from_status=current, to_status=target, requires_reason=requires_reason
    )
