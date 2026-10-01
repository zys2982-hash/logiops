"""客户通知出入参（基线文档 §10.3 /notifications）。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class NotificationUpdate(BaseModel):
    expected_version: int | None = None
    subject: str | None = Field(default=None, max_length=128)
    content: str | None = Field(default=None, max_length=4000)


class NotificationSkip(BaseModel):
    reason: str = Field(min_length=1, max_length=255)
    expected_version: int | None = None


class NotificationSend(BaseModel):
    expected_version: int | None = None


class NotificationOut(BaseModel):
    id: int
    exception_id: int | None = None
    customer_id: int | None = None
    channel: str | None = None
    subject: str | None = None
    content: str | None = None
    ai_draft_content: str | None = None
    status: str | None = None
    approved_by: int | None = None
    approved_at: str | None = None
    sent_at: str | None = None
    source_approval_id: int | None = None
    version: int | None = None
    created_at: str | None = None
    updated_at: str | None = None


class NotificationListOut(BaseModel):
    items: list[NotificationOut]
    total: int


__all__ = ["NotificationListOut", "NotificationOut", "NotificationSend", "NotificationSkip", "NotificationUpdate"]
