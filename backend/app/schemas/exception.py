"""异常出入参（基线文档 §10.3 /exceptions，形状对齐 §12.2 详情页）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ExceptionCreate(BaseModel):
    """POST /exceptions 手工建单（MANUAL）；带 level 时仅 ADMIN+ 可用。"""

    order_id: int
    type: str | None = Field(
        default=None,
        description=(
            "建单原因（可省略）。界面不再让用户选类型：省略时按订单现场推"
            "（有车→VEHICLE_BREAKDOWN，否则 DELAY_RISK）；"
            "界面上的「当前问题」由风险因子实时推导，见响应里的 current_type"
        ),
    )
    occurred_at: datetime
    note: str = Field(min_length=1, max_length=500)
    level: str | None = Field(default=None, description="LOW/MEDIUM/HIGH/CRITICAL，仅 ADMIN+")


class ExceptionPatch(BaseModel):
    """PATCH /exceptions/{id}：expected_version 必填（§10.1 幂等要求）。"""

    expected_version: int
    assigned_to: int | None = None
    remark: str | None = Field(default=None, max_length=255)


class ExceptionConfirm(BaseModel):
    expected_version: int | None = None
    note: str | None = Field(default=None, max_length=500)


class ExceptionAnalyze(BaseModel):
    expected_version: int | None = None


class ExceptionResolve(BaseModel):
    expected_version: int
    note: str = Field(min_length=1, max_length=500)


class ExceptionClose(BaseModel):
    expected_version: int
    reason_code: str = Field(default="MANUAL", description="INVALID/DELIVERED/MANUAL/FORCED_CLOSE")
    note: str | None = Field(default=None, max_length=500)


class ExceptionClearIssue(BaseModel):
    """POST /exceptions/{id}/clear-vehicle-issue：只解除「车辆故障」问题，**不结束整单**。"""

    expected_version: int
    note: str | None = Field(default=None, max_length=500)


class CarrierMessageCreate(BaseModel):
    raw_text: str = Field(min_length=1, max_length=4000)
    channel: str = "MANUAL_PASTE"
    sender_name: str | None = Field(default=None, max_length=64)
    received_at: datetime | None = None
    expected_version: int | None = None


class CarrierMessageOut(BaseModel):
    id: int
    exception_id: int | None = None
    order_id: int | None = None
    channel: str | None = None
    sender_name: str | None = None
    sender_role: str | None = None
    raw_text: str | None = None
    received_at: str | None = None
    parse_status: str | None = None
    parse_result: dict[str, Any] | None = None
    parser_version: str | None = None
    parse_error: str | None = None
    created_at: str | None = None


class AnalyzeAccepted(BaseModel):
    analysis_id: int
    status: str
    reused: bool = False
    reused_from_id: int | None = None
    exception_status: str | None = None
    input_hash: str | None = None
    error_code: str | None = None
    error_message: str | None = None


class MessageAccepted(BaseModel):
    message_id: int
    parse_status: str
    exception_id: int
    exception_status: str | None = None
    transitioned: bool = False
    parse_result: dict[str, Any] | None = None
    eta: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None


class ExceptionEventOut(BaseModel):
    id: int
    exception_id: int | None = None
    event_type: str | None = None
    actor_type: str | None = None
    actor_id: int | None = None
    from_status: str | None = None
    to_status: str | None = None
    note: str | None = None
    detail: dict[str, Any] | None = None
    occurred_at: str | None = None


class ExceptionEventPage(BaseModel):
    items: list[ExceptionEventOut]
    total: int
    page: int
    page_size: int


class ExceptionOut(BaseModel):
    id: int
    case_no: str | None = None
    order_id: int | None = None
    customer_id: int | None = None
    vehicle_id: int | None = None
    carrier_id: int | None = None
    type: str | None = None
    current_type: str | None = None
    current_level: str | None = None
    current_risk_score: int | None = None
    level: str | None = None
    status: str | None = None
    detected_by: str | None = None
    detection_rule: str | None = None
    occurred_at: str | None = None
    stall_since: str | None = None
    root_cause: dict[str, Any] | None = None
    impact_summary: str | None = None
    promised_delivery_at: str | None = None
    expected_eta_at: str | None = None
    current_eta_at: str | None = None
    delivered_at: str | None = None
    delay_minutes: int | None = None
    sla_delay_minutes: int | None = None
    sla_breached: bool | None = None
    risk_score: int | None = None
    risk_factors: list[dict[str, Any]] | None = None
    risk_explanation: dict[str, Any] | None = None
    assigned_to: int | None = None
    resolved_at: str | None = None
    closed_at: str | None = None
    close_reason: str | None = None
    merged_count: int | None = None
    version: int | None = None
    created_at: str | None = None
    updated_at: str | None = None
    order_no: str | None = None
    customer_name: str | None = None
    vehicle_plate: str | None = None
    order: dict[str, Any] | None = None
    customer: dict[str, Any] | None = None
    vehicle: dict[str, Any] | None = None
    sla: dict[str, Any] | None = None
    tracking_events: list[dict[str, Any]] | None = None
    latest_carrier_message: dict[str, Any] | None = None
    history: dict[str, Any] | None = None
    latest_analysis: dict[str, Any] | None = None
    counts: dict[str, int] | None = None


class ExceptionPage(BaseModel):
    items: list[ExceptionOut]
    total: int
    page: int
    page_size: int


class ExceptionTimelinePage(BaseModel):
    items: list[ExceptionEventOut]
    total: int
    page: int
    page_size: int


__all__ = [
    "AnalyzeAccepted",
    "CarrierMessageCreate",
    "CarrierMessageOut",
    "ExceptionAnalyze",
    "ExceptionClearIssue",
    "ExceptionClose",
    "ExceptionConfirm",
    "ExceptionCreate",
    "ExceptionEventOut",
    "ExceptionOut",
    "ExceptionPage",
    "ExceptionPatch",
    "ExceptionResolve",
    "ExceptionTimelinePage",
    "MessageAccepted",
]
