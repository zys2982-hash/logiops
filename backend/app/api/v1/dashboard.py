"""Dashboard 统计（基线文档 §10.3【Dashboard】、§12.1 首屏）。

口径：
- "今日"按业务时区 Asia/Shanghai 的自然日切分（clock.today_local）。
- 全部统计都经 ``Repos``（workspace 过滤），不会跨租户。
"""

from __future__ import annotations

from datetime import UTC, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import RequestContext, require
from app.core.clock import now_utc, today_local
from app.core.permissions import Perm
from app.models.enums import OPEN_EXCEPTION_STATUSES, ExceptionLevel, ExceptionStatus
from app.models.exception import ExceptionCase
from app.models.transport import Order
from app.schemas.common import to_iso_z
from app.schemas.dashboard import DashboardSummary, ExceptionBrief, TrendPoint, TrendResponse

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

ViewCtx = Annotated[RequestContext, Depends(require(Perm.DASHBOARD_VIEW))]

TREND_DAYS = 7
HIGH_RISK_LEVELS = [str(ExceptionLevel.HIGH), str(ExceptionLevel.CRITICAL)]
OPEN_STATUSES = [str(status) for status in OPEN_EXCEPTION_STATUSES]


def _local_day_start_utc_naive(day_start_local) -> object:
    return day_start_local.astimezone(UTC).replace(tzinfo=None)


def _trend(ctx: RequestContext, days: int = TREND_DAYS) -> list[TrendPoint]:
    start_local = today_local()
    points: list[TrendPoint] = []
    for offset in range(days - 1, -1, -1):
        day_start_local = start_local - timedelta(days=offset)
        day_end_local = day_start_local + timedelta(days=1)
        start = _local_day_start_utc_naive(day_start_local)
        end = _local_day_start_utc_naive(day_end_local)
        in_day = [ExceptionCase.created_at >= start, ExceptionCase.created_at < end]
        points.append(
            TrendPoint(
                date=day_start_local.strftime("%Y-%m-%d"),
                detected=ctx.repos.exceptions.count(*in_day),
                breached=ctx.repos.exceptions.count(*in_day, ExceptionCase.sla_breached.is_(True)),
                resolved=ctx.repos.exceptions.count(
                    ExceptionCase.resolved_at.is_not(None),
                    ExceptionCase.resolved_at >= start,
                    ExceptionCase.resolved_at < end,
                ),
                closed=ctx.repos.exceptions.count(
                    ExceptionCase.closed_at.is_not(None),
                    ExceptionCase.closed_at >= start,
                    ExceptionCase.closed_at < end,
                ),
            )
        )
    return points


def _brief(case: ExceptionCase) -> ExceptionBrief:
    order = case.order
    customer = case.customer
    return ExceptionBrief(
        id=case.id,
        case_no=case.case_no,
        order_id=case.order_id,
        order_no=order.order_no if order else None,
        customer_name=customer.name if customer else None,
        type=str(case.type),
        level=str(case.level),
        status=str(case.status),
        risk_score=case.risk_score,
        sla_breached=bool(case.sla_breached),
        sla_delay_minutes=case.sla_delay_minutes,
        expected_eta_at=to_iso_z(case.expected_eta_at),
        updated_at=to_iso_z(case.updated_at),
    )


@router.get("/summary", response_model=DashboardSummary, summary="首屏统计卡 + 高风险 Top5 + 近 7 天趋势")
def summary(ctx: ViewCtx) -> DashboardSummary:
    now = now_utc()
    day_start = _local_day_start_utc_naive(today_local())
    repos = ctx.repos

    by_status = repos.exceptions.count_by_status()
    by_level = repos.exceptions.count_by_level()

    high_risk = repos.exceptions.count(
        ExceptionCase.level.in_(HIGH_RISK_LEVELS),
        ExceptionCase.status.in_(OPEN_STATUSES),
    )
    high_risk_top = [
        _brief(case)
        for case in repos.exceptions.all(
            filters=[
                ExceptionCase.level.in_(HIGH_RISK_LEVELS),
                ExceptionCase.status.in_(OPEN_STATUSES),
            ],
            order_by=[ExceptionCase.risk_score.desc(), ExceptionCase.id.desc()],
        )[:5]
    ]

    return DashboardSummary(
        generated_at=now,
        now_utc=to_iso_z(now),
        business_date=today_local().strftime("%Y-%m-%d"),
        today_orders=repos.orders.count(Order.created_at >= day_start),
        in_transit=repos.orders.count_in_transit(),
        delayed_orders=repos.orders.count(
            Order.current_eta_at.is_not(None),
            Order.promised_delivery_at.is_not(None),
            Order.current_eta_at > Order.promised_delivery_at,
        ),
        exceptions_total=sum(by_status.values()),
        open_exceptions=sum(count for status, count in by_status.items() if status in OPEN_STATUSES),
        high_risk=high_risk,
        pending=by_status.get(str(ExceptionStatus.DETECTED), 0),
        # 历史状态（旧 CONFIRMING/ANALYZING）在 4 状态模型里视同"处理中"
        processing=by_status.get(str(ExceptionStatus.PROCESSING), 0)
        + by_status.get(str(ExceptionStatus.CONFIRMING), 0)
        + by_status.get(str(ExceptionStatus.ANALYZING), 0),
        resolving=by_status.get(str(ExceptionStatus.RESOLVED), 0),
        resolved=by_status.get(str(ExceptionStatus.RESOLVED), 0),
        closed=by_status.get(str(ExceptionStatus.CLOSED), 0),
        sla_breached=repos.exceptions.count(ExceptionCase.sla_breached.is_(True)),
        sla_breached_open=repos.exceptions.count(
            ExceptionCase.sla_breached.is_(True), ExceptionCase.status.in_(OPEN_STATUSES)
        ),
        by_level=by_level,
        by_status=by_status,
        high_risk_top=high_risk_top,
        trend=_trend(ctx),
    )


@router.get("/trend", response_model=TrendResponse, summary="近 N 天异常与违约趋势（默认 7 天）")
def trend(ctx: ViewCtx, days: Annotated[int, Query(ge=1, le=30)] = TREND_DAYS) -> TrendResponse:
    points = _trend(ctx, days)
    return TrendResponse(
        days=days,
        start_date=points[0].date if points else "",
        end_date=points[-1].date if points else "",
        items=points,
        trend=points,
    )


__all__ = ["router"]
