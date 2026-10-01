"""T01 user / T02 workspace / T03 workspace_member / T22 system_setting。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, PkMixin, PKType, TimestampMixin


class User(Base, PkMixin, TimestampMixin):
    __tablename__ = "user"

    email: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(32))
    avatar_url: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE", server_default="ACTIVE")
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime)


class Workspace(Base, PkMixin, TimestampMixin):
    __tablename__ = "workspace"

    name: Mapped[str] = mapped_column(String(64), nullable=False)
    code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    owner_user_id: Mapped[int] = mapped_column(PKType, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE", server_default="ACTIVE")


class WorkspaceMember(Base, PkMixin, TimestampMixin):
    __tablename__ = "workspace_member"
    __table_args__ = (UniqueConstraint("workspace_id", "user_id", name="uq_workspace_member_ws_user"),)

    workspace_id: Mapped[int] = mapped_column(PKType, nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(PKType, nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="OPERATOR")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE", server_default="ACTIVE")
    invited_by: Mapped[int | None] = mapped_column(PKType)
    joined_at: Mapped[datetime | None] = mapped_column(DateTime)


class SystemSetting(Base, TimestampMixin):
    __tablename__ = "system_setting"

    setting_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    setting_value: Mapped[str | None] = mapped_column(String(255))
