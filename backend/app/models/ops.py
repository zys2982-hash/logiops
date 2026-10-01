"""T19 audit_log / T20 knowledge_doc / T21 knowledge_chunk。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CHAR, JSON, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.clock import utcnow_naive
from app.db.base import Base, PkMixin, PKType, TimestampMixin


class AuditLog(Base, PkMixin):
    """追加写，不提供更新/删除接口。"""

    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_log_ws_time", "workspace_id", "occurred_at"),
        Index("ix_audit_log_resource", "resource_type", "resource_id"),
    )

    workspace_id: Mapped[int] = mapped_column(PKType, nullable=False, index=True)
    actor_type: Mapped[str] = mapped_column(String(8), nullable=False, default="USER")
    actor_id: Mapped[int | None] = mapped_column(PKType)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_type: Mapped[str | None] = mapped_column(String(32))
    resource_id: Mapped[int | None] = mapped_column(PKType)
    before_json: Mapped[dict | None] = mapped_column(JSON)
    after_json: Mapped[dict | None] = mapped_column(JSON)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="MANUAL")
    request_id: Mapped[str | None] = mapped_column(String(36))
    ip: Mapped[str | None] = mapped_column(String(45))
    user_agent: Mapped[str | None] = mapped_column(String(255))
    occurred_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow_naive, index=True)


class KnowledgeDoc(Base, PkMixin, TimestampMixin):
    __tablename__ = "knowledge_doc"

    workspace_id: Mapped[int | None] = mapped_column(PKType, nullable=True, index=True)  # NULL = 全局共享
    doc_code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    category: Mapped[str | None] = mapped_column(String(32))
    version: Mapped[str] = mapped_column(String(16), default="v1")
    source_path: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE", server_default="ACTIVE")
    checksum: Mapped[str | None] = mapped_column(CHAR(64))


class KnowledgeChunk(Base, PkMixin):
    __tablename__ = "knowledge_chunk"

    doc_id: Mapped[int] = mapped_column(PKType, ForeignKey("knowledge_doc.id"), nullable=False, index=True)
    chunk_no: Mapped[int] = mapped_column(Integer, nullable=False)
    section_path: Mapped[str | None] = mapped_column(String(255))
    content: Mapped[str] = mapped_column(Text, nullable=False)
    token_estimate: Mapped[int | None] = mapped_column(Integer)
    checksum: Mapped[str | None] = mapped_column(CHAR(64))
