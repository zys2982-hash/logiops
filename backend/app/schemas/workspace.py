"""工作区与成员 schema（基线文档 §10.3【工作区】）。"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.models.enums import Role
from app.schemas.common import OptUtcDateTime, ORMModel, UtcDateTime


class WorkspaceOut(ORMModel):
    id: int
    name: str
    code: str
    status: str
    owner_user_id: int
    created_at: UtcDateTime
    updated_at: OptUtcDateTime = None
    role: str | None = None
    member_count: int | None = None


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    code: str = Field(min_length=1, max_length=32, description="工作区短码，全局唯一")
    owner_user_id: int | None = Field(default=None, description="缺省为创建者")


class MemberOut(BaseModel):
    id: int
    user_id: int
    email: str
    name: str
    role: str
    status: str
    joined_at: OptUtcDateTime = None
    created_at: OptUtcDateTime = None

    @classmethod
    def from_pair(cls, member: object, user: object) -> MemberOut:
        return cls(
            id=member.id,
            user_id=member.user_id,
            email=getattr(user, "email", ""),
            name=getattr(user, "name", ""),
            role=str(member.role),
            status=str(member.status),
            joined_at=getattr(member, "joined_at", None),
            created_at=getattr(member, "created_at", None),
        )


class MemberCreate(BaseModel):
    email: str = Field(min_length=3, max_length=128, description="按邮箱直接添加（不做邮件邀请）")
    role: Role = Field(default=Role.OPERATOR, description="不能直接添加为 OWNER，转让请用 PATCH")


class MemberUpdate(BaseModel):
    role: Role
    expected_version: int | None = Field(default=None, description="乐观锁版本（可选）")


__all__ = [
    "MemberCreate",
    "MemberOut",
    "MemberUpdate",
    "WorkspaceCreate",
    "WorkspaceOut",
]
