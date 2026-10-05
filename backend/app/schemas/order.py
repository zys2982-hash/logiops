"""订单出入参（基线文档 §10.3 /orders）。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class OrderCreate(BaseModel):
    customer_id: int
    origin_city: str = Field(min_length=1, max_length=64)
    dest_city: str = Field(min_length=1, max_length=64)
    order_no: str | None = Field(default=None, max_length=32)
    cargo_desc: str | None = Field(default=None, max_length=128)
    weight_ton: float | None = Field(default=None, ge=0)
    distance_km: int | None = Field(default=None, gt=0)
    remark: str | None = Field(default=None, max_length=255)


class OrderUpdate(BaseModel):
    """PATCH /orders/{id}：填 vehicle_id/carrier_id 时按状态机派车。"""

    expected_version: int | None = None
    carrier_id: int | None = None
    vehicle_id: int | None = None
    driver_id: int | None = None
    origin_city: str | None = Field(default=None, max_length=64)
    dest_city: str | None = Field(default=None, max_length=64)
    cargo_desc: str | None = Field(default=None, max_length=128)
    weight_ton: float | None = Field(default=None, ge=0)
    distance_km: int | None = Field(default=None, gt=0)
    remark: str | None = Field(default=None, max_length=255)


class OpenExceptionBrief(BaseModel):
    id: int
    case_no: str | None = None
    type: str | None = None
    current_type: str | None = None
    current_level: str | None = None
    current_risk_score: int | None = None
    level: str | None = None
    status: str | None = None
    risk_score: int | None = None
    sla_breached: bool | None = None
    sla_delay_minutes: int | None = None


class SlaSnapshot(BaseModel):
    rule_id: int | None = None
    rule_name: str | None = None
    scope_type: str | None = None
    scope_value: str | None = None
    deadline_offset_hours: int | None = None
    max_delay_minutes: int | None = None
    promised_delivery_at: str | None = None
    expected_eta_at: str | None = None
    delay_minutes: int | None = None
    breached: bool | None = None


class OrderDeliveredAtCorrect(BaseModel):
    """PATCH /orders/{id}/delivered-at —— 修正**实际送达时间**（送达录错时用）。

    延误单本质是"实际送达 vs 承诺送达"的比较结果，所以纠错入口是这里；
    修正后后端立刻重算该订单的延误单（仍违约→重算；不再违约→自动解决）。
    """

    delivered_at: datetime
    note: str | None = Field(default=None, max_length=500)


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    order_no: str | None = None
    status: str | None = None
    customer_id: int | None = None
    customer_name: str | None = None
    customer_code: str | None = None
    customer_level: str | None = None
    origin_city: str | None = None
    dest_city: str | None = None
    cargo_desc: str | None = None
    weight_ton: float | None = None
    distance_km: int | None = None
    vehicle_id: int | None = None
    vehicle_plate: str | None = None
    carrier_id: int | None = None
    carrier_name: str | None = None
    driver_id: int | None = None
    driver_name: str | None = None
    dispatched_at: str | None = None
    promised_delivery_at: str | None = None
    original_eta_at: str | None = None
    current_eta_at: str | None = None
    delivered_at: str | None = None
    sla_rule_id: int | None = None
    remark: str | None = None
    version: int | None = None
    created_at: str | None = None
    updated_at: str | None = None
    last_tracking_at: str | None = None
    open_exception: OpenExceptionBrief | None = None
    sla: SlaSnapshot | None = None


class OrderPage(BaseModel):
    items: list[OrderOut]
    total: int
    page: int
    page_size: int


__all__ = ["OpenExceptionBrief", "OrderCreate", "OrderOut", "OrderPage", "OrderUpdate", "SlaSnapshot"]
