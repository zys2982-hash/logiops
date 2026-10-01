"""跟进任务出入参（基线文档 §10.3 /followups）。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class FollowupCreate(BaseModel):
    exception_id: int
    title: str = Field(min_length=1, max_length=128)
    content: str | None = Field(default=None, max_length=500)
    assignee_user_id: int | None = None
    due_at: datetime | None = None
    priority: str = "NORMAL"
    source: str = "MANUAL"


class FollowupUpdate(BaseModel):
    expected_version: int | None = None
    title: str | None = Field(default=None, max_length=128)
    content: str | None = Field(default=None, max_length=500)
    assignee_user_id: int | None = None
    due_at: datetime | None = None
    priority: str | None = None
    status: str | None = Field(default=None, description="OPEN/DONE/CANCELLED")
    remark: str | None = Field(default=None, max_length=255)


class FollowupOut(BaseModel):
    id: int
    exception_id: int | None = None
    title: str | None = None
    content: str | None = None
    assignee_user_id: int | None = None
    assignee_name: str | None = None
    due_at: str | None = None
    priority: str | None = None
    status: str | None = None
    source: str | None = None
    source_approval_id: int | None = None
    done_at: str | None = None
    done_by: int | None = None
    remark: str | None = None
    version: int | None = None
    created_at: str | None = None
    updated_at: str | None = None


class FollowupListOut(BaseModel):
    items: list[FollowupOut]
    total: int


__all__ = ["FollowupCreate", "FollowupListOut", "FollowupOut", "FollowupUpdate"]
