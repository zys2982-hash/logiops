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
    # 对外 4 状态（用户口径）：待确认 → 处理中 → 已解决 → 已关闭
    ExceptionStatus.DETECTED: {ExceptionStatus.PROCESSING, ExceptionStatus.CLOSED},
    ExceptionStatus.PROCESSING: {ExceptionStatus.RESOLVED, ExceptionStatus.CLOSED},
    ExceptionStatus.RESOLVED: {ExceptionStatus.CLOSED},
    ExceptionStatus.CLOSED: set(),
    # ↓ 历史兼容：旧的 CONFIRMING / ANALYZING 视同 PROCESSING（等价流转、可按 4 状态收口），
    #   不再作为任何流程的目标状态；数据迁移脚本会把旧行改写成 PROCESSING。
    ExceptionStatus.CONFIRMING: {
        ExceptionStatus.PROCESSING,
        ExceptionStatus.RESOLVED,
        ExceptionStatus.CLOSED,
    },
    ExceptionStatus.ANALYZING: {
        ExceptionStatus.PROCESSING,
        ExceptionStatus.RESOLVED,
        ExceptionStatus.CLOSED,
    },
}

# 旧状态：只读兼容用（读取时归一化为 PROCESSING，见 enums.normalize_exception_status）
LEGACY_EXCEPTION_STATUSES: frozenset[ExceptionStatus] = frozenset(
    {ExceptionStatus.CONFIRMING, ExceptionStatus.ANALYZING}
)

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
