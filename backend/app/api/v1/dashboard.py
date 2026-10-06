"""Dashboard 统计（基线文档 §10.3【Dashboard】、§12.1 首屏）。

口径：
- "今日"按业务时区 Asia/Shanghai 的自然日切分（clock.today_local）。
- 全部统计都经 ``Repos``（workspace 过滤），不会跨租户。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
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
from app.services import eta_flow
from app.services.common import now_naive

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

ViewCtx = Annotated[RequestContext, Depends(require(Perm.DASHBOARD_VIEW))]

TREND_DAYS = 7
HIGH_RISK_LEVELS = [str(ExceptionLevel.HIGH), str(ExceptionLevel.CRITICAL)]
OPEN_STATUSES = [str(status) for status in OPEN_EXCEPTION_STATUSES]
# **未结束**（≠ "未关闭"）：已解决 / 已关闭的单没有"当前风险"（列表显示"无风险 · 0 分"），
# 所以它们不该再算进"高风险 / 严重"卡片与高风险 Top5（用户口径 2026-10-06：
# 「高风险的判定应该依据当前异常中心的状态」，与异常中心「等级」列同口径）。
ACTIVE_STATUSES = [
    str(ExceptionStatus.DETECTED),
    str(ExceptionStatus.PROCESSING),
    str(ExceptionStatus.CONFIRMING),
    str(ExceptionStatus.ANALYZING),
]


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


def _brief(case: ExceptionCase, *, now: datetime | None = None) -> ExceptionBrief:
    order = case.order
    customer = case.customer
    carrier = case.carrier
    occurred_at = case.occurred_at
    age_minutes = None
    if now is not None and occurred_at is not None:
        age_minutes = max(int((now - occurred_at).total_seconds() // 60), 0)
    return ExceptionBrief(
        id=case.id,
        case_no=case.case_no,
        order_id=case.order_id,
        order_no=order.order_no if order else None,
        customer_name=customer.name if customer else None,
        carrier_name=carrier.name if carrier else None,
        type=str(case.type),
        # 与异常中心同一套口径：界面显示"当前问题 / 当前风险"（已结束 → 无风险 0 分），
        # 存档的 level / risk_score 也一并带上（详情页的"历史判定"要用）
        current_type=eta_flow.current_case_type(case),
        current_level=eta_flow.current_case_risk(case)[0],
        current_risk_score=eta_flow.current_case_risk(case)[1],
        level=str(case.level),
        status=str(case.status),
        risk_score=case.risk_score,
        sla_breached=bool(case.sla_breached),
        sla_delay_minutes=case.sla_delay_minutes,
        expected_eta_at=to_iso_z(case.expected_eta_at),
        occurred_at=to_iso_z(occurred_at),
        age_minutes=age_minutes,
        updated_at=to_iso_z(case.updated_at),
    )


@router.get(
    "/summary",
    response_model=DashboardSummary,
    summary="首屏统计卡 + 待处置异常 + 高风险 Top5 + 近 7 天趋势",
)
def summary(ctx: ViewCtx) -> DashboardSummary:
    now = now_utc()
    business_now = now_naive()  # 业务时钟（真实时间或演示虚拟时钟），"已挂时长"按它算
    day_start = _local_day_start_utc_naive(today_local())
    repos = ctx.repos

    by_status = repos.exceptions.count_by_status()
    by_level = repos.exceptions.count_by_level()

    # 高风险只数**未结束**的单（已解决 / 已关闭 → 当前风险 0 分，不算高风险），
    # 与异常中心「等级」列显示的当前风险同一口径。
    high_risk = repos.exceptions.count(
        ExceptionCase.level.in_(HIGH_RISK_LEVELS),
        ExceptionCase.status.in_(ACTIVE_STATUSES),
    )
    high_risk_top = [
        _brief(case, now=business_now)
        for case in repos.exceptions.all(
            filters=[
                ExceptionCase.level.in_(HIGH_RISK_LEVELS),
                ExceptionCase.status.in_(ACTIVE_STATUSES),
            ],
            order_by=[ExceptionCase.risk_score.desc(), ExceptionCase.id.desc()],
        )[:5]
    ]

    # 待处置异常：**未结束**（待确认 / 处理中）按"当前风险倒序 → 挂得越久越靠前"，
    # 这就是运营首屏该看的"现在要处理什么"（2026-10-06 用它替掉了 7 天趋势折线图）。
    action_queue = [
        _brief(case, now=business_now)
        for case in repos.exceptions.all(
            filters=[ExceptionCase.status.in_(ACTIVE_STATUSES)],
            order_by=[ExceptionCase.risk_score.desc(), ExceptionCase.occurred_at.asc()],
        )[:8]
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
        action_queue=action_queue,
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
