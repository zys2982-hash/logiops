"""T08 order / T09 tracking_event。"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.clock import utcnow_naive
from app.db.base import (
    Base,
    PkMixin,
    PKType,
    SoftDeleteMixin,
    TimestampMixin,
    VersionMixin,
    WorkspaceScopedMixin,
)


class Order(Base, PkMixin, WorkspaceScopedMixin, TimestampMixin, VersionMixin, SoftDeleteMixin):
    __tablename__ = "order"
    __table_args__ = (UniqueConstraint("workspace_id", "order_no", name="uq_order_ws_order_no"),)

    order_no: Mapped[str] = mapped_column(String(32), nullable=False)
    customer_id: Mapped[int] = mapped_column(PKType, ForeignKey("customer.id"), nullable=False, index=True)
    carrier_id: Mapped[int | None] = mapped_column(PKType, ForeignKey("carrier.id"))
    vehicle_id: Mapped[int | None] = mapped_column(PKType, ForeignKey("vehicle.id"))
    driver_id: Mapped[int | None] = mapped_column(PKType, ForeignKey("driver.id"))
    origin_city: Mapped[str] = mapped_column(String(64), nullable=False)
    dest_city: Mapped[str] = mapped_column(String(64), nullable=False)
    cargo_desc: Mapped[str | None] = mapped_column(String(128))
    weight_ton: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    distance_km: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="CREATED", server_default="CREATED")
    sla_rule_id: Mapped[int | None] = mapped_column(PKType, ForeignKey("sla_rule.id"))
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime)
    promised_delivery_at: Mapped[datetime | None] = mapped_column(DateTime)
    original_eta_at: Mapped[datetime | None] = mapped_column(DateTime)
    current_eta_at: Mapped[datetime | None] = mapped_column(DateTime)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime)
    # 「预计到达时间」：运营在订单详情页**手工登记**的独立字段（2026-10-08 用户需求）。
    # 与 promised_delivery_at（SLA 规则算的承诺到达）、delivered_at（订单事实/实际送达）都不同：
    # · **不产生轨迹事件、不进运输轨迹时间线**；
    # · 只是一个可随时取用的独立数据，后续若要用于延误判定等逻辑再说（本次只加字段）。
    planned_delivery_at: Mapped[datetime | None] = mapped_column(DateTime)
    remark: Mapped[str | None] = mapped_column(String(255))

    customer = relationship("Customer", lazy="joined")
    carrier = relationship("Carrier", lazy="joined")
    vehicle = relationship("Vehicle", lazy="joined")
    driver = relationship("Driver", lazy="joined")
    sla_rule = relationship("SlaRule", lazy="joined")


class TrackingEvent(Base, PkMixin):
    __tablename__ = "tracking_event"

    workspace_id: Mapped[int] = mapped_column(PKType, nullable=False, index=True)
    order_id: Mapped[int] = mapped_column(PKType, ForeignKey("order.id"), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(24), nullable=False)
    city: Mapped[str] = mapped_column(String(64), nullable=False)
    address: Mapped[str | None] = mapped_column(String(128))
    lat: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    lng: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    occurred_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(16), default="MOCK", server_default="MOCK")
    speed_kmh: Mapped[Decimal | None] = mapped_column(Numeric(5, 1))
    payload_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow_naive)
