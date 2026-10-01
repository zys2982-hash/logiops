"""T11 exception_case / T12 exception_event / T13 carrier_message / T17 followup_task / T18 notification。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.clock import utcnow_naive
from app.db.base import (
    AuditCreatorMixin,
    Base,
    PkMixin,
    PKType,
    SoftDeleteMixin,
    TimestampMixin,
    VersionMixin,
    WorkspaceScopedMixin,
)


class ExceptionCase(
    Base, PkMixin, WorkspaceScopedMixin, TimestampMixin, VersionMixin, SoftDeleteMixin, AuditCreatorMixin
):
    __tablename__ = "exception_case"
    __table_args__ = (
        UniqueConstraint("workspace_id", "case_no", name="uq_exception_case_ws_case_no"),
        Index("ix_exception_case_ws_status_level", "workspace_id", "status", "level"),
        Index("ix_exception_case_order", "workspace_id", "order_id"),
        Index("ix_exception_case_breach", "workspace_id", "sla_breached", "created_at"),
    )

    case_no: Mapped[str] = mapped_column(String(32), nullable=False)
    order_id: Mapped[int] = mapped_column(PKType, ForeignKey("order.id"), nullable=False)
    customer_id: Mapped[int] = mapped_column(PKType, ForeignKey("customer.id"), nullable=False)
    vehicle_id: Mapped[int | None] = mapped_column(PKType, ForeignKey("vehicle.id"))
    carrier_id: Mapped[int | None] = mapped_column(PKType, ForeignKey("carrier.id"))
    type: Mapped[str] = mapped_column(String(32), nullable=False, default="VEHICLE_BREAKDOWN")
    level: Mapped[str] = mapped_column(String(16), nullable=False, default="MEDIUM")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="DETECTED", index=True)
    detected_by: Mapped[str] = mapped_column(String(16), nullable=False, default="SYSTEM")
    detection_rule: Mapped[str | None] = mapped_column(String(32))
    occurred_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow_naive)
    stall_since: Mapped[datetime | None] = mapped_column(DateTime)
    root_cause_code: Mapped[str | None] = mapped_column(String(32))
    root_cause_note: Mapped[str | None] = mapped_column(String(255))
    impact_summary: Mapped[str | None] = mapped_column(String(255))
    promised_delivery_at: Mapped[datetime | None] = mapped_column(DateTime)
    current_eta_at: Mapped[datetime | None] = mapped_column(DateTime)
    expected_eta_at: Mapped[datetime | None] = mapped_column(DateTime)
    sla_delay_minutes: Mapped[int | None] = mapped_column(Integer)
    sla_breached: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    risk_score: Mapped[int | None] = mapped_column(Integer)
    risk_factors_json: Mapped[list | None] = mapped_column(JSON)
    assigned_to: Mapped[int | None] = mapped_column(PKType)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime)
    close_reason: Mapped[str | None] = mapped_column(String(32))
    merged_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    order = relationship("Order", lazy="joined")
    customer = relationship("Customer", lazy="joined")
    vehicle = relationship("Vehicle", lazy="joined")
    carrier = relationship("Carrier", lazy="joined")


class ExceptionEvent(Base, PkMixin):
    __tablename__ = "exception_event"
    __table_args__ = (Index("ix_exception_event_case_time", "exception_id", "occurred_at"),)

    workspace_id: Mapped[int] = mapped_column(PKType, nullable=False, index=True)
    exception_id: Mapped[int] = mapped_column(PKType, ForeignKey("exception_case.id"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(24), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(8), nullable=False, default="SYSTEM")
    actor_id: Mapped[int | None] = mapped_column(PKType)
    from_status: Mapped[str | None] = mapped_column(String(16))
    to_status: Mapped[str | None] = mapped_column(String(16))
    detail_json: Mapped[dict | None] = mapped_column(JSON)
    note: Mapped[str | None] = mapped_column(String(500))
    occurred_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow_naive, index=True)


class CarrierMessage(Base, PkMixin, WorkspaceScopedMixin, TimestampMixin, AuditCreatorMixin):
    __tablename__ = "carrier_message"

    exception_id: Mapped[int] = mapped_column(PKType, ForeignKey("exception_case.id"), nullable=False, index=True)
    order_id: Mapped[int] = mapped_column(PKType, ForeignKey("order.id"), nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(16), nullable=False, default="MANUAL_PASTE")
    sender_name: Mapped[str | None] = mapped_column(String(64))
    sender_role: Mapped[str] = mapped_column(String(16), default="CARRIER")
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow_naive)
    parse_status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")
    parse_result_json: Mapped[dict | None] = mapped_column(JSON)
    parser_version: Mapped[str | None] = mapped_column(String(32))
    parse_error: Mapped[str | None] = mapped_column(String(255))
    attachment_urls_json: Mapped[list | None] = mapped_column(JSON)


class FollowupTask(Base, PkMixin, WorkspaceScopedMixin, TimestampMixin, VersionMixin):
    __tablename__ = "followup_task"

    exception_id: Mapped[int] = mapped_column(PKType, ForeignKey("exception_case.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    content: Mapped[str | None] = mapped_column(String(500))
    assignee_user_id: Mapped[int | None] = mapped_column(PKType)
    due_at: Mapped[datetime | None] = mapped_column(DateTime)
    priority: Mapped[str] = mapped_column(String(16), default="NORMAL")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="OPEN", server_default="OPEN")
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="MANUAL")
    source_approval_id: Mapped[int | None] = mapped_column(PKType)
    done_at: Mapped[datetime | None] = mapped_column(DateTime)
    done_by: Mapped[int | None] = mapped_column(PKType)
    remark: Mapped[str | None] = mapped_column(String(255))


class Notification(Base, PkMixin, WorkspaceScopedMixin, TimestampMixin, VersionMixin):
    __tablename__ = "notification"

    exception_id: Mapped[int] = mapped_column(PKType, ForeignKey("exception_case.id"), nullable=False, index=True)
    customer_id: Mapped[int] = mapped_column(PKType, ForeignKey("customer.id"), nullable=False)
    channel: Mapped[str] = mapped_column(String(16), nullable=False, default="MANUAL_COPY")
    subject: Mapped[str | None] = mapped_column(String(128))
    content: Mapped[str] = mapped_column(Text, nullable=False)
    ai_draft_content: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="DRAFT", server_default="DRAFT")
    approved_by: Mapped[int | None] = mapped_column(PKType)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime)
    source_approval_id: Mapped[int | None] = mapped_column(PKType)
