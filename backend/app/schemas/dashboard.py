"""Dashboard schema（基线文档 §10.3【Dashboard】）。"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas.common import UtcDateTime


class ExceptionBrief(BaseModel):
    id: int
    case_no: str
    order_id: int
    order_no: str | None = None
    customer_name: str | None = None
    type: str
    current_type: str | None = None
    level: str
    status: str
    risk_score: int | None = None
    sla_breached: bool = False
    sla_delay_minutes: int | None = None
    expected_eta_at: str | None = None
    updated_at: str | None = None


class TrendPoint(BaseModel):
    date: str = Field(description="本地日期 YYYY-MM-DD")
    detected: int = 0
    breached: int = 0
    resolved: int = 0
    closed: int = 0


class TrendResponse(BaseModel):
    days: int
    start_date: str
    end_date: str
    items: list[TrendPoint] = Field(default_factory=list)
    trend: list[TrendPoint] = Field(default_factory=list, description="兼容别名：与 items 同值")


class DashboardSummary(BaseModel):
    generated_at: UtcDateTime
    now_utc: str
    business_date: str = Field(description="业务口径（Asia/Shanghai）当天 YYYY-MM-DD")

    # 订单
    today_orders: int = 0
    in_transit: int = 0
    delayed_orders: int = 0

    # 异常
    exceptions_total: int = 0
    open_exceptions: int = 0
    high_risk: int = 0
    pending: int = 0
    processing: int = 0
    resolving: int = 0
    resolved: int = 0
    closed: int = 0
    sla_breached: int = 0
    sla_breached_open: int = 0

    by_level: dict[str, int] = Field(default_factory=dict)
    by_status: dict[str, int] = Field(default_factory=dict)
    high_risk_top: list[ExceptionBrief] = Field(default_factory=list)
    trend: list[TrendPoint] = Field(default_factory=list)


__all__ = ["DashboardSummary", "ExceptionBrief", "TrendPoint", "TrendResponse"]
