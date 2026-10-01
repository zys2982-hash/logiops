"""客户通知服务（基线文档 §10.3 /notifications）。

- 只有 DRAFT 可编辑正文，且保留 ai_draft_content（AI 原稿留痕）。
- 模拟发送 → SENT_MOCK + 审计；跳过 → SKIPPED（必填 reason）。
- 通知正文的来源优先级：审批单 final_payload > AI 草稿 > 确定性模板（AI 不可用时的兜底）。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import AppError, ErrorCode
from app.models.enums import (
    ActorType,
    AuditSource,
    ExceptionEventType,
    NotificationChannel,
    NotificationStatus,
)
from app.models.exception import Notification
from app.repositories import Repos
from app.services import ai_bridge, read_models
from app.services.common import (
    add_event,
    bump_version,
    check_version,
    now_naive,
    require_text,
    write_audit,
)

VALID_STATUS = {member.value for member in NotificationStatus}


class NotificationService:
    def __init__(self, repos: Repos) -> None:
        self.repos = repos
        self.session: Session = repos.session

    def get(self, notification_id: int) -> Notification:
        return self.repos.notifications.get_or_404(notification_id, "通知不存在")

    def create(
        self,
        *,
        exception_id: int,
        content: str | None = None,
        subject: str | None = None,
        channel: str = NotificationChannel.MANUAL_COPY,
        status: str = NotificationStatus.DRAFT,
        ai_draft_content: str | None = None,
        source_approval_id: int | None = None,
        use_ai_draft: bool = True,
        actor_id: int | None = None,
    ) -> Notification:
        exception = self.repos.exceptions.get_or_404(exception_id, "异常单不存在")
        draft_subject, draft_content, origin = subject, content, "MANUAL"

        if not draft_content:
            if use_ai_draft:
                draft, origin = ai_bridge.notice_from_ai_or_template(
                    self.session, self.repos, exception.id, None
                )
                draft_subject = draft_subject or draft["subject"]
                draft_content = draft["content"]
            else:
                draft = ai_bridge.fallback_notice(read_models.exception_facts(self.repos, exception.id))
                draft_subject = draft_subject or draft["subject"]
                draft_content = draft["content"]
                origin = "TEMPLATE"

        status_value = str(status or NotificationStatus.DRAFT).upper()
        if status_value not in VALID_STATUS:
            status_value = str(NotificationStatus.DRAFT)
        notification = Notification(
            workspace_id=int(self.repos.workspace_id or 0),
            exception_id=exception.id,
            customer_id=exception.customer_id,
            channel=str(channel or NotificationChannel.MANUAL_COPY).upper(),
            subject=(draft_subject or "")[:128] or None,
            content=require_text(draft_content, "content", max_len=4000),
            ai_draft_content=(ai_draft_content or draft_content)[:4000],
            status=status_value,
            source_approval_id=source_approval_id,
        )
        if status_value == str(NotificationStatus.SENT_MOCK):
            notification.approved_by = actor_id
            notification.approved_at = now_naive()
            notification.sent_at = now_naive()
        self.repos.notifications.add(notification)

        add_event(
            self.session,
            self.repos,
            exception_id=exception.id,
            event_type=str(ExceptionEventType.APPROVED),
            from_status=exception.status,
            to_status=exception.status,
            actor_type=ActorType.AI if origin == "AI" else ActorType.USER,
            actor_id=actor_id,
            note=f"生成客户通知草稿（来源：{'AI' if origin == 'AI' else '确定性模板'}）",
            detail={
                "notification_id": notification.id,
                "status": notification.status,
                "draft_origin": origin,
                "source_approval_id": source_approval_id,
            },
        )
        write_audit(
            self.session,
            self.repos,
            "notification.created",
            resource_type="notification",
            resource_id=notification.id,
            actor_type=ActorType.SYSTEM,
            actor_id=actor_id,
            after={
                "exception_id": exception.id,
                "status": notification.status,
                "draft_origin": origin,
                "source_approval_id": source_approval_id,
            },
            source=AuditSource.APPROVED_AI if source_approval_id else AuditSource.MANUAL,
        )
        return notification

    def update_content(
        self,
        notification_id: int,
        *,
        subject: str | None = None,
        content: str | None = None,
        expected_version: int | None = None,
        actor_id: int | None = None,
    ) -> Notification:
        notification = self.get(notification_id)
        check_version(notification, expected_version, "通知")
        if str(notification.status) != str(NotificationStatus.DRAFT):
            raise AppError(
                ErrorCode.STATE_TRANSITION_INVALID,
                "只有草稿状态的通知可以编辑",
                {"status": notification.status},
            )
        before = {"subject": notification.subject, "content": notification.content}
        if subject is not None:
            notification.subject = subject[:128]
        if content is not None:
            notification.content = require_text(content, "content", max_len=4000)
        bump_version(notification)
        self.repos.notifications.save(notification)
        write_audit(
            self.session,
            self.repos,
            "notification.updated",
            resource_type="notification",
            resource_id=notification.id,
            actor_id=actor_id,
            before=before,
            after={"subject": notification.subject, "content": notification.content},
        )
        return notification

    def mark_sent(
        self,
        notification_id: int,
        *,
        expected_version: int | None = None,
        actor_id: int | None = None,
    ) -> Notification:
        notification = self.get(notification_id)
        check_version(notification, expected_version, "通知")
        if str(notification.status) == str(NotificationStatus.SENT_MOCK):
            return notification
        if str(notification.status) not in {str(NotificationStatus.DRAFT), str(NotificationStatus.APPROVED)}:
            raise AppError(
                ErrorCode.STATE_TRANSITION_INVALID,
                f"通知状态为 {notification.status}，不能发送",
                {"status": notification.status},
            )
        notification.status = str(NotificationStatus.SENT_MOCK)
        notification.approved_by = notification.approved_by or actor_id
        notification.approved_at = notification.approved_at or now_naive()
        notification.sent_at = now_naive()
        bump_version(notification)
        self.repos.notifications.save(notification)
        exception = self.repos.exceptions.get(notification.exception_id)
        if exception is not None:
            add_event(
                self.session,
                self.repos,
                exception_id=exception.id,
                event_type=str(ExceptionEventType.EXECUTED),
                from_status=exception.status,
                to_status=exception.status,
                actor_type=ActorType.USER,
                actor_id=actor_id,
                note="客户通知已模拟发送",
                detail={"notification_id": notification.id, "channel": notification.channel},
            )
        write_audit(
            self.session,
            self.repos,
            "notification.sent_mock",
            resource_type="notification",
            resource_id=notification.id,
            actor_id=actor_id,
            after={"status": notification.status, "sent_at": notification.sent_at},
        )
        return notification

    def skip(
        self,
        notification_id: int,
        *,
        reason: str,
        expected_version: int | None = None,
        actor_id: int | None = None,
    ) -> Notification:
        notification = self.get(notification_id)
        check_version(notification, expected_version, "通知")
        reason_text = require_text(reason, "reason", max_len=255)
        if str(notification.status) == str(NotificationStatus.SENT_MOCK):
            raise AppError(
                ErrorCode.STATE_TRANSITION_INVALID,
                "已发送的通知不能跳过",
                {"status": notification.status},
            )
        notification.status = str(NotificationStatus.SKIPPED)
        bump_version(notification)
        self.repos.notifications.save(notification)
        exception = self.repos.exceptions.get(notification.exception_id)
        if exception is not None:
            add_event(
                self.session,
                self.repos,
                exception_id=exception.id,
                event_type=str(ExceptionEventType.COMMENT),
                from_status=exception.status,
                to_status=exception.status,
                actor_type=ActorType.USER,
                actor_id=actor_id,
                note=f"通知被跳过：{reason_text}",
                detail={"notification_id": notification.id},
            )
        write_audit(
            self.session,
            self.repos,
            "notification.skipped",
            resource_type="notification",
            resource_id=notification.id,
            actor_id=actor_id,
            after={"status": notification.status, "reason": reason_text},
        )
        return notification

    def list_for_exception(self, exception_id: int) -> list[dict[str, Any]]:
        self.repos.exceptions.get_or_404(exception_id, "异常单不存在")
        return [
            serialize(notification)
            for notification in self.repos.notifications.list_for_case(exception_id)
        ]


def serialize(notification: Notification) -> dict[str, Any]:
    return {
        "id": notification.id,
        "exception_id": notification.exception_id,
        "customer_id": notification.customer_id,
        "channel": notification.channel,
        "subject": notification.subject,
        "content": notification.content,
        "ai_draft_content": notification.ai_draft_content,
        "status": notification.status,
        "approved_by": notification.approved_by,
        "approved_at": read_models.iso(notification.approved_at),
        "sent_at": read_models.iso(notification.sent_at),
        "source_approval_id": notification.source_approval_id,
        "version": notification.version,
        "created_at": read_models.iso(notification.created_at),
        "updated_at": read_models.iso(notification.updated_at),
    }


__all__ = ["NotificationService", "serialize"]
