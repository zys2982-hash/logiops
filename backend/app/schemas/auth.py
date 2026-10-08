"""认证相关 schema（基线文档 §10.3【认证】）。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.masking import mask_phone
from app.schemas.common import OptUtcDateTime, ORMModel, UtcDateTime


class RegisterRequest(BaseModel):
    email: str = Field(min_length=3, max_length=128, description="登录邮箱")
    password: str = Field(min_length=8, max_length=64, description="密码，至少 8 位")
    name: str = Field(min_length=1, max_length=64, description="姓名")

    @field_validator("email")
    @classmethod
    def _check_email(cls, value: str) -> str:
        email = value.strip().lower()
        if "@" not in email or email.startswith("@") or email.endswith("@"):
            raise ValueError("邮箱格式不正确")
        return email


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=128)
    password: str = Field(min_length=1, max_length=64)

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, value: str) -> str:
        return value.strip().lower()


class ChangePasswordRequest(BaseModel):
    """修改自己的密码：必须验原密码（防"拿着 token 就能改密码"）。"""

    old_password: str = Field(min_length=1, max_length=64, description="原密码")
    new_password: str = Field(min_length=8, max_length=64, description="新密码，至少 8 位")

    @field_validator("new_password")
    @classmethod
    def _check_strength(cls, value: str) -> str:
        if value.strip() != value:
            raise ValueError("新密码首尾不能有空格")
        if not any(char.isalpha() for char in value) or not any(char.isdigit() for char in value):
            raise ValueError("新密码需同时包含字母和数字")
        return value

    @model_validator(mode="after")
    def _reject_unchanged(self) -> ChangePasswordRequest:
        if self.old_password == self.new_password:
            raise ValueError("新密码不能与原密码相同")
        return self


class ChangePasswordResponse(BaseModel):
    ok: bool = True
    message: str = "密码已修改，请用新密码登录"


class UserOut(BaseModel):
    """用户出参：手机号脱敏（§8.7）。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    name: str
    phone: str | None = None
    avatar_url: str | None = None
    status: str
    last_login_at: OptUtcDateTime = None
    created_at: UtcDateTime

    @model_validator(mode="after")
    def _mask_phone(self) -> UserOut:
        self.phone = mask_phone(self.phone)
        return self

    @classmethod
    def from_model(cls, user: object) -> UserOut:
        return cls.model_validate(
            {
                "id": user.id,  # type: ignore[attr-defined]
                "email": user.email,  # type: ignore[attr-defined]
                "name": user.name,  # type: ignore[attr-defined]
                "phone": mask_phone(getattr(user, "phone", None)),
                "avatar_url": getattr(user, "avatar_url", None),
                "status": getattr(user, "status", "ACTIVE"),
                "last_login_at": getattr(user, "last_login_at", None),
                "created_at": getattr(user, "created_at", None),
            }
        )


class WorkspaceBrief(ORMModel):
    id: int
    name: str
    code: str
    role: str


class MeOut(BaseModel):
    """/auth/me：当前用户 + 当前工作区角色与权限 + 我加入的工作区列表。"""

    user: UserOut
    role: str | None = None
    permissions: list[str] = Field(default_factory=list)
    workspace_id: int | None = None
    workspace: WorkspaceBrief | None = None
    workspaces: list[WorkspaceBrief] = Field(default_factory=list)


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut


class RegisterResponse(BaseModel):
    user: UserOut
    default_workspace_id: int | None = None


class LogoutResponse(BaseModel):
    ok: bool = True
    message: str = "已登出（服务端不维护 token 黑名单，请前端丢弃本地 token）"


__all__ = [
    "ChangePasswordRequest",
    "ChangePasswordResponse",
    "LoginRequest",
    "LoginResponse",
    "LogoutResponse",
    "MeOut",
    "RegisterRequest",
    "RegisterResponse",
    "UserOut",
    "WorkspaceBrief",
]
