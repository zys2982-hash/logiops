"""审计日志统一入口（追加写，不可改）。"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from sqlalchemy.orm import Session

from app.core.clock import utcnow_naive
from app.models.ops import AuditLog


def to_jsonable(value: Any) -> Any:
    """把 ORM/枚举/时间等转成可 JSON 序列化的结构。"""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [to_jsonable(item) for item in value]
    if hasattr(value, "to_dict"):
        return to_jsonable(value.to_dict())
    return str(value)


def record_audit(
    session: Session,
    *,
    workspace_id: int,
    action: str,
    resource_type: str | None = None,
    resource_id: int | None = None,
    actor_type: str = "USER",
    actor_id: int | None = None,
    before: dict | None = None,
    after: dict | None = None,
    source: str = "MANUAL",
    request_id: str | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
) -> AuditLog:
    entry = AuditLog(
        workspace_id=workspace_id,
        actor_type=actor_type,
        actor_id=actor_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        before_json=to_jsonable(before) if before else None,
        after_json=to_jsonable(after) if after else None,
        source=source,
        request_id=request_id,
        ip=ip,
        user_agent=(user_agent or "")[:255] or None,
        occurred_at=utcnow_naive(),
    )
    session.add(entry)
    session.flush()
    return entry


def record_audit_isolated(**kwargs: Any) -> None:
    """用独立会话写审计，立即提交。

    专门给"抛错前"的场景用（权限不足 403、跨租户 404）：请求级 Session 会在
    get_db 的 except 分支里回滚，若共用会话，安全审计会一起被回滚掉（基线 §9.1 第 6 条）。
    """
    from app.db.session import get_sessionmaker  # 局部导入避免循环依赖

    session = get_sessionmaker()()
    try:
        record_audit(session, **kwargs)
        session.commit()
    except Exception:  # pragma: no cover - 审计失败不能影响主流程
        session.rollback()
    finally:
        session.close()
