"""SQLAlchemy 声明式基类与公共列（基线文档 §7.1）。

跨库可移植：主键用 BigInteger + sqlite 变体，布尔用 Boolean。
枚举一律 VARCHAR(32) + 应用层 StrEnum 校验（不用 DB ENUM）。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, MetaData, SmallInteger
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core.clock import utcnow_naive

# SQLite 需要 INTEGER PRIMARY KEY 才能自增
PKType = BigInteger().with_variant(Integer, "sqlite")

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    def to_dict(self) -> dict:
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        pk = getattr(self, "id", None)
        return f"<{self.__class__.__name__} id={pk}>"


class PkMixin:
    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)


class TimestampMixin:
    """时间戳走系统时钟（ReplayClock 下即 Demo 业务时间），保证可复现。"""

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow_naive, onupdate=utcnow_naive
    )


class WorkspaceScopedMixin:
    workspace_id: Mapped[int] = mapped_column(
        PKType, ForeignKey("workspace.id", ondelete="RESTRICT"), nullable=False, index=True
    )


class VersionMixin:
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")


class SoftDeleteMixin:
    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")


class AuditCreatorMixin:
    created_by: Mapped[int | None] = mapped_column(PKType, nullable=True)


class FlagMixin:
    """小整数标记列（0/1）的公共定义。"""

    @staticmethod
    def small_flag(default: int = 0) -> Mapped[int]:
        return mapped_column(SmallInteger, nullable=False, default=default, server_default=str(default))
