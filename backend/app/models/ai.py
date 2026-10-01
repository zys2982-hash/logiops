"""T14 ai_analysis / T15 ai_analysis_step / T16 approval。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CHAR, JSON, Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, PkMixin, PKType, TimestampMixin, VersionMixin, WorkspaceScopedMixin

# raw_output 可能较长：MySQL 用 MEDIUMTEXT，其他方言退化为 TEXT
LongText = Text().with_variant(mysql.MEDIUMTEXT, "mysql")


class AiAnalysis(Base, PkMixin, WorkspaceScopedMixin, TimestampMixin):
    __tablename__ = "ai_analysis"
    __table_args__ = (
        Index("ix_ai_analysis_case_time", "workspace_id", "exception_id", "created_at"),
    )

    exception_id: Mapped[int] = mapped_column(PKType, ForeignKey("exception_case.id"), nullable=False)
    analysis_no: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    task_type: Mapped[str] = mapped_column(String(32), nullable=False, default="ANALYZE_EXCEPTION")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING", server_default="PENDING")
    triggered_by: Mapped[int | None] = mapped_column(PKType)
    input_hash: Mapped[str | None] = mapped_column(CHAR(64), index=True)
    model: Mapped[str | None] = mapped_column(String(64))
    prompt_version: Mapped[str | None] = mapped_column(String(16))
    output_json: Mapped[dict | None] = mapped_column(JSON)
    raw_output: Mapped[str | None] = mapped_column(LongText)
    risk_level_calculated: Mapped[str | None] = mapped_column(String(16))
    error_code: Mapped[str | None] = mapped_column(String(32))
    error_message: Mapped[str | None] = mapped_column(String(255))
    tokens_in: Mapped[int | None] = mapped_column(Integer)
    tokens_out: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    reused_from_id: Mapped[int | None] = mapped_column(PKType)
    is_replay: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)

    steps = relationship("AiAnalysisStep", lazy="selectin", order_by="AiAnalysisStep.step_no")


class AiAnalysisStep(Base, PkMixin):
    __tablename__ = "ai_analysis_step"

    analysis_id: Mapped[int] = mapped_column(PKType, ForeignKey("ai_analysis.id"), nullable=False, index=True)
    step_no: Mapped[int] = mapped_column(Integer, nullable=False)
    step_type: Mapped[str] = mapped_column(String(16), nullable=False)
    tool_name: Mapped[str | None] = mapped_column(String(32))
    args_json: Mapped[dict | None] = mapped_column(JSON)
    result_summary: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="OK")
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime | None] = mapped_column(DateTime)


class Approval(Base, PkMixin, WorkspaceScopedMixin, TimestampMixin, VersionMixin):
    __tablename__ = "approval"
    __table_args__ = (
        Index("ix_approval_ws_status", "workspace_id", "status"),
        Index("ix_approval_case_status", "exception_id", "status"),
    )

    exception_id: Mapped[int] = mapped_column(PKType, ForeignKey("exception_case.id"), nullable=False)
    analysis_id: Mapped[int | None] = mapped_column(PKType, ForeignKey("ai_analysis.id"))
    action_type: Mapped[str] = mapped_column(String(24), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(24))
    target_id: Mapped[int | None] = mapped_column(PKType)
    ai_payload_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    final_payload_json: Mapped[dict | None] = mapped_column(JSON)
    diff_json: Mapped[dict | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING", server_default="PENDING")
    decided_by: Mapped[int | None] = mapped_column(PKType)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime)
    reject_reason: Mapped[str | None] = mapped_column(String(255))
    executed_at: Mapped[datetime | None] = mapped_column(DateTime)
    execution_result_json: Mapped[dict | None] = mapped_column(JSON)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime)
