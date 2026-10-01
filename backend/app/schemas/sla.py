"""SLA 规则 schema（基线文档 §7.4 T10、§8.3）。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import SlaScopeType
from app.schemas.common import OptUtcDateTime, UtcDateTime


class SlaRuleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    scope_type: SlaScopeType = SlaScopeType.DEFAULT
    scope_value: str | None = Field(
        default=None, max_length=32, description="CUSTOMER_LEVEL 填等级 / CUSTOMER 填客户 code"
    )
    deadline_offset_hours: int = Field(default=30, ge=1, le=24 * 30, description="承诺到达 = 发车时间 + N 小时")
    max_delay_minutes: int = Field(default=30, ge=0, le=24 * 60, description="允许延迟，超出即违约")
    priority: int = Field(default=100, ge=0, le=10000, description="同作用域取数值最小者")
    action_policy_json: dict[str, Any] | None = None
    description: str | None = Field(default=None, max_length=255)
    is_active: bool = True


class SlaRuleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    scope_type: SlaScopeType | None = None
    scope_value: str | None = Field(default=None, max_length=32)
    deadline_offset_hours: int | None = Field(default=None, ge=1, le=24 * 30)
    max_delay_minutes: int | None = Field(default=None, ge=0, le=24 * 60)
    priority: int | None = Field(default=None, ge=0, le=10000)
    action_policy_json: dict[str, Any] | None = None
    description: str | None = Field(default=None, max_length=255)
    is_active: bool | None = None
    expected_version: int | None = None


class SlaRuleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    workspace_id: int
    name: str
    scope_type: str
    scope_value: str | None = None
    deadline_offset_hours: int
    max_delay_minutes: int
    priority: int
    action_policy_json: dict[str, Any] | None = None
    description: str | None = None
    is_active: bool
    version: int
    created_at: UtcDateTime
    updated_at: OptUtcDateTime = None

    @classmethod
    def from_model(cls, rule: object) -> SlaRuleOut:
        return cls(
            id=rule.id,  # type: ignore[attr-defined]
            workspace_id=rule.workspace_id,  # type: ignore[attr-defined]
            name=rule.name,  # type: ignore[attr-defined]
            scope_type=str(rule.scope_type),  # type: ignore[attr-defined]
            scope_value=getattr(rule, "scope_value", None),
            deadline_offset_hours=int(rule.deadline_offset_hours),  # type: ignore[attr-defined]
            max_delay_minutes=int(rule.max_delay_minutes),  # type: ignore[attr-defined]
            priority=int(getattr(rule, "priority", 100)),
            action_policy_json=getattr(rule, "action_policy_json", None),
            description=getattr(rule, "description", None),
            is_active=bool(getattr(rule, "is_active", True)),
            version=int(getattr(rule, "version", 1)),
            created_at=rule.created_at,
            updated_at=getattr(rule, "updated_at", None),
        )


__all__ = ["SlaRuleCreate", "SlaRuleOut", "SlaRuleUpdate"]
