"""审计日志 schema（基线文档 §7.4 T19、§10.3【审计】）。审计只读。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from app.schemas.common import OptUtcDateTime, UtcDateTime


class AuditLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    workspace_id: int
    actor_type: str
    actor_id: int | None = None
    action: str
    resource_type: str | None = None
    resource_id: int | None = None
    before_json: dict[str, Any] | None = None
    after_json: dict[str, Any] | None = None
    source: str
    request_id: str | None = None
    ip: str | None = None
    user_agent: str | None = None
    occurred_at: UtcDateTime


class AuditLogPageMeta(BaseModel):
    total: int
    page: int
    page_size: int
    occurred_from: OptUtcDateTime = None
    occurred_to: OptUtcDateTime = None


__all__ = ["AuditLogOut", "AuditLogPageMeta"]
