"""客户通知接口（基线文档 §10.3 【通知】/notifications）。"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from app.api.deps import RequestContext, require
from app.core.permissions import Perm
from app.schemas.notification import (
    NotificationOut,
    NotificationSend,
    NotificationSkip,
    NotificationUpdate,
)
from app.services.notifications import NotificationService, serialize

router = APIRouter(tags=["notifications"])

NotificationView = Annotated[RequestContext, Depends(require(Perm.EXCEPTION_VIEW))]
NotificationWrite = Annotated[RequestContext, Depends(require(Perm.NOTIFICATION_APPROVE))]


@router.get("/exceptions/{exception_id}/notifications", response_model=list[NotificationOut], summary="异常下的通知")
def list_notifications(ctx: NotificationView, exception_id: int) -> Any:
    return NotificationService(ctx.repos).list_for_exception(exception_id)


@router.get("/notifications/{notification_id}", response_model=NotificationOut, summary="通知详情")
def get_notification(ctx: NotificationView, notification_id: int) -> Any:
    return serialize(NotificationService(ctx.repos).get(notification_id))


@router.patch(
    "/notifications/{notification_id}",
    response_model=NotificationOut,
    summary="编辑正文（仅 DRAFT，保留 ai_draft_content）",
)
def update_notification(ctx: NotificationWrite, notification_id: int, payload: NotificationUpdate) -> Any:
    notification = NotificationService(ctx.repos).update_content(
        notification_id,
        subject=payload.subject,
        content=payload.content,
        expected_version=payload.expected_version,
        actor_id=ctx.user.id,
    )
    return serialize(notification)


@router.post(
    "/notifications/{notification_id}/mark-sent",
    response_model=NotificationOut,
    summary="模拟发送 → SENT_MOCK",
)
def mark_sent(ctx: NotificationWrite, notification_id: int, payload: NotificationSend | None = None) -> Any:
    notification = NotificationService(ctx.repos).mark_sent(
        notification_id,
        expected_version=payload.expected_version if payload else None,
        actor_id=ctx.user.id,
    )
    return serialize(notification)


@router.post(
    "/notifications/{notification_id}/skip",
    response_model=NotificationOut,
    summary="跳过 → SKIPPED（需 reason）",
)
def skip_notification(ctx: NotificationWrite, notification_id: int, payload: NotificationSkip) -> Any:
    notification = NotificationService(ctx.repos).skip(
        notification_id,
        reason=payload.reason,
        expected_version=payload.expected_version,
        actor_id=ctx.user.id,
    )
    return serialize(notification)


__all__ = ["router"]
