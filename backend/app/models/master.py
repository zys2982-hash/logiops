"""T04 customer / T05 carrier / T06 vehicle / T07 driver / T10 sla_rule。"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import (
    Base,
    PkMixin,
    PKType,
    SoftDeleteMixin,
    TimestampMixin,
    VersionMixin,
    WorkspaceScopedMixin,
)


class Customer(Base, PkMixin, WorkspaceScopedMixin, TimestampMixin, VersionMixin, SoftDeleteMixin):
    __tablename__ = "customer"
    __table_args__ = (UniqueConstraint("workspace_id", "code", name="uq_customer_ws_code"),)

    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    level: Mapped[str] = mapped_column(String(16), nullable=False, default="NORMAL", server_default="NORMAL")
    contact_name: Mapped[str | None] = mapped_column(String(64))
    contact_phone: Mapped[str | None] = mapped_column(String(32))
    contact_email: Mapped[str | None] = mapped_column(String(128))
    notify_pref: Mapped[str] = mapped_column(String(32), default="MANUAL_COPY", server_default="MANUAL_COPY")
    remark: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE", server_default="ACTIVE")


class Carrier(Base, PkMixin, WorkspaceScopedMixin, TimestampMixin, VersionMixin, SoftDeleteMixin):
    __tablename__ = "carrier"
    __table_args__ = (UniqueConstraint("workspace_id", "code", name="uq_carrier_ws_code"),)

    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    contact_name: Mapped[str | None] = mapped_column(String(64))
    contact_phone: Mapped[str | None] = mapped_column(String(32))
    service_level: Mapped[str] = mapped_column(String(16), default="NORMAL", server_default="NORMAL")
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE", server_default="ACTIVE")
    remark: Mapped[str | None] = mapped_column(String(255))


class Vehicle(Base, PkMixin, WorkspaceScopedMixin, TimestampMixin, VersionMixin, SoftDeleteMixin):
    __tablename__ = "vehicle"
    __table_args__ = (UniqueConstraint("workspace_id", "plate_no", name="uq_vehicle_ws_plate"),)

    plate_no: Mapped[str] = mapped_column(String(16), nullable=False)
    vehicle_type: Mapped[str | None] = mapped_column(String(32))
    capacity_ton: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    carrier_id: Mapped[int | None] = mapped_column(PKType, ForeignKey("carrier.id"))
    status: Mapped[str] = mapped_column(String(16), default="IDLE", server_default="IDLE")
    current_driver_id: Mapped[int | None] = mapped_column(PKType, ForeignKey("driver.id"))
    current_city: Mapped[str | None] = mapped_column(String(64))
    remark: Mapped[str | None] = mapped_column(String(255))


class Driver(Base, PkMixin, WorkspaceScopedMixin, TimestampMixin, VersionMixin, SoftDeleteMixin):
    __tablename__ = "driver"

    name: Mapped[str] = mapped_column(String(64), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(32))
    carrier_id: Mapped[int | None] = mapped_column(PKType, ForeignKey("carrier.id"))
    license_no: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="AVAILABLE", server_default="AVAILABLE")


class SlaRule(Base, PkMixin, WorkspaceScopedMixin, TimestampMixin, VersionMixin):
    __tablename__ = "sla_rule"
    __table_args__ = (UniqueConstraint("workspace_id", "scope_type", "scope_value", name="uq_sla_rule_ws_scope"),)

    name: Mapped[str] = mapped_column(String(64), nullable=False)
    scope_type: Mapped[str] = mapped_column(String(16), nullable=False, default="DEFAULT")
    scope_value: Mapped[str | None] = mapped_column(String(32))
    deadline_offset_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    max_delay_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    action_policy_json: Mapped[dict | None] = mapped_column(JSON)
    description: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
