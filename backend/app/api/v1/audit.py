"""审计日志查询（基线文档 §10.3【审计】）。只读，追加写表不可改。

筛选：``?resource_type&resource_id&actor_id&action&occurred_from&occurred_to&page&page_size``
时间入参接受 ISO8601（``Z`` / ``+08:00`` / 无时区），DB 存朴素 UTC。
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import PageDep, RequestContext, require
from app.core.clock import parse_dt
from app.core.errors import validation_error
from app.core.permissions import Perm
from app.schemas.audit import AuditLogOut
from app.schemas.common import Page, page_of

router = APIRouter(prefix="/audit-logs", tags=["audit"])

ViewCtx = Annotated[RequestContext, Depends(require(Perm.AUDIT_VIEW))]


def _parse_boundary(value: str | None, field: str) -> datetime | None:
    if not value:
        return None
    try:
        return parse_dt(value).replace(tzinfo=None)
    except (TypeError, ValueError) as exc:
        raise validation_error(
            "时间格式不正确，应为 ISO8601",
            fields=[{"loc": field, "msg": str(exc)}],
        ) from exc


@router.get("", response_model=Page[AuditLogOut], summary="审计日志（筛选 + 分页，倒序）")
def list_audit_logs(
    ctx: ViewCtx,
    page: PageDep,
    resource_type: Annotated[str | None, Query(max_length=32)] = None,
    resource_id: Annotated[int | None, Query(ge=1)] = None,
    actor_id: Annotated[int | None, Query(ge=1)] = None,
    action: Annotated[str | None, Query(max_length=64, description="模糊匹配")] = None,
    occurred_from: Annotated[str | None, Query(description="ISO8601，含边界")] = None,
    occurred_to: Annotated[str | None, Query(description="ISO8601，含边界")] = None,
) -> Page[AuditLogOut]:
    rows, total = ctx.repos.audit.search(
        resource_type=resource_type,
        resource_id=resource_id,
        actor_id=actor_id,
        action=action,
        occurred_from=_parse_boundary(occurred_from, "occurred_from"),
        occurred_to=_parse_boundary(occurred_to, "occurred_to"),
        page=page.page,
        page_size=page.page_size,
    )
    return page_of(AuditLogOut, rows, total, page.page, page.page_size)


__all__ = ["router"]
