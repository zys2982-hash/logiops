"""轨迹出入参（基线文档 §10.3 /orders/{id}/tracking-events）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class TrackingEventCreate(BaseModel):
    event_type: str = Field(description="DEPART/ARRIVE/STOP/RESUME/REPAIR_START/REPAIR_END/DELIVER/NOTE")
    city: str = Field(min_length=1, max_length=64)
    occurred_at: datetime | None = None
    source: str = Field(default="OPERATOR")
    speed_kmh: float | None = Field(default=None, ge=0, le=200)
    address: str | None = Field(default=None, max_length=128)
    payload: dict[str, Any] | None = None


class TrackingEventOut(BaseModel):
    id: int
    order_id: int | None = None
    event_type: str | None = None
    city: str | None = None
    address: str | None = None
    occurred_at: str | None = None
    source: str | None = None
    speed_kmh: float | None = None
    payload: dict[str, Any] | None = None
    eta_method: str | None = None
    current_eta_at: str | None = None
    exception_id: int | None = None


class TrackingPage(BaseModel):
    items: list[TrackingEventOut]
    total: int
    page: int
    page_size: int


__all__ = ["TrackingEventCreate", "TrackingEventOut", "TrackingPage"]
