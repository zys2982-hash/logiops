"""服务层公共工具：时间归一、乐观锁、状态机入口、异常时间线、审计、编号。

设计约束（基线文档 §8）：
- 状态变更只能通过 `apply_transition`，它内部强制走 `state_machine.plan_transition`。
- DB 里时间一律朴素 UTC；对外 ISO8601 由 read_models.iso 负责。
- 乐观锁：写接口带 expected_version，不匹配 → 409 OPTIMISTIC_LOCK_CONFLICT。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.audit import record_audit, to_jsonable
from app.core.clock import now_utc
from app.core.errors import AppError, ErrorCode, validation_error
from app.models.ai import AiAnalysis
from app.models.enums import ActorType, AuditSource
from app.models.exception import ExceptionEvent
from app.models.transport import Order
from app.repositories import Repos
from app.rules import state_machine


def now_naive() -> datetime:
    """当前业务时间（ReplayClock 感知）的朴素 UTC 表示。"""
    return now_utc().astimezone(UTC).replace(tzinfo=None)


def to_naive_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def parse_iso_naive(value: Any) -> datetime | None:
    """解析 ISO 字符串/日期为朴素 UTC；朴素输入视为 UTC（AI 输出契约）。"""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return to_naive_utc(value)
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).replace(tzinfo=None)


def require_text(value: Any, field: str, *, max_len: int | None = None) -> str:
    text = "" if value is None else str(value).strip()
    if not text:
        raise validation_error(f"{field} 不能为空", fields=[{"loc": field, "msg": "必填"}])
    if max_len is not None and len(text) > max_len:
        raise validation_error(
            f"{field} 长度不能超过 {max_len}",
            fields=[{"loc": field, "msg": f"最长 {max_len} 字符"}],
        )
    return text


def check_version(obj: Any, expected: int | None, resource: str = "资源") -> None:
    """乐观锁校验：expected_version 缺省表示不校验。"""
    if expected is None:
        return
    current = obj.version if hasattr(obj, "version") else None
    if current is None or int(current) != int(expected):
        raise AppError(
            ErrorCode.OPTIMISTIC_LOCK_CONFLICT,
            f"{resource}已被他人更新，请刷新后重试",
            {"expected_version": int(expected), "current_version": current},
        )


def bump_version(obj: Any) -> None:
    if hasattr(obj, "version"):
        obj.version = int(obj.version or 0) + 1


def apply_transition(
    obj: Any,
    kind: state_machine.EntityKind | str,
    target: str,
    *,
    reason: str | None = None,
) -> state_machine.TransitionPlan:
    """校验并落地状态流转；非法流转抛 409 STATE_TRANSITION_INVALID。"""
    plan = state_machine.plan_transition(kind, obj.status, target, reason)
    obj.status = plan.to_status
    return plan


def add_event(
    session: Session,
    repos: Repos,
    *,
    exception_id: int,
    event_type: str,
    from_status: str | None = None,
    to_status: str | None = None,
    actor_type: str = ActorType.SYSTEM,
    actor_id: int | None = None,
    note: str | None = None,
    detail: dict[str, Any] | None = None,
) -> ExceptionEvent:
    event = ExceptionEvent(
        workspace_id=int(repos.workspace_id or 0),
        exception_id=exception_id,
        event_type=str(event_type),
        actor_type=str(actor_type),
        actor_id=actor_id,
        from_status=from_status,
        to_status=to_status,
        detail_json=to_jsonable(detail) if detail else None,
        note=note[:500] if note else None,
        occurred_at=now_naive(),
    )
    session.add(event)
    session.flush()
    return event


def write_audit(
    session: Session,
    repos: Repos,
    action: str,
    *,
    resource_type: str | None = None,
    resource_id: int | None = None,
    actor_type: str = ActorType.USER,
    actor_id: int | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    source: str = AuditSource.MANUAL,
    request_id: str | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
) -> Any:
    return record_audit(
        session,
        workspace_id=int(repos.workspace_id or 0),
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        actor_type=str(actor_type),
        actor_id=actor_id,
        before=before,
        after=after,
        source=str(source),
        request_id=request_id,
        ip=ip,
        user_agent=user_agent,
    )


def _day_stamp(moment: datetime | None = None) -> str:
    return (to_naive_utc(moment) or now_naive()).strftime("%Y%m%d")


def next_case_no(repos: Repos, *, moment: datetime | None = None) -> str:
    prefix = f"EX{_day_stamp(moment)}"
    return f"{prefix}{repos.exceptions.next_sequence(prefix):04d}"


def next_analysis_no(repos: Repos, *, moment: datetime | None = None) -> str:
    prefix = f"AI{_day_stamp(moment)}"
    stmt = select(func.count()).select_from(AiAnalysis).where(AiAnalysis.analysis_no.like(f"{prefix}%"))
    total = int(repos.session.scalar(stmt) or 0) + 1
    return f"{prefix}{total:06d}"


def next_order_no(repos: Repos, *, moment: datetime | None = None) -> str:
    prefix = f"SO{_day_stamp(moment)}"
    conditions = [Order.order_no.like(f"{prefix}%")]
    if repos.workspace_id is not None:
        conditions.append(Order.workspace_id == repos.workspace_id)
    stmt = select(func.count()).select_from(Order).where(*conditions)
    seq = int(repos.session.scalar(stmt) or 0) + 1
    return f"{prefix}{seq:04d}"


__all__ = [
    "add_event",
    "apply_transition",
    "bump_version",
    "check_version",
    "next_analysis_no",
    "next_case_no",
    "next_order_no",
    "now_naive",
    "parse_iso_naive",
    "require_text",
    "to_naive_utc",
    "write_audit",
]
