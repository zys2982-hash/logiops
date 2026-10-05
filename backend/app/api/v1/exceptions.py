"""异常接口（基线文档 §10.3 【异常】/exceptions）。

状态机 / AI 调用 / 审批生成全部在 ExceptionService，Router 只做鉴权与编排。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.deps import PageDep, RequestContext, require
from app.core.errors import perm_denied, validation_error
from app.core.permissions import Perm, has_perm
from app.schemas.exception import (
    AnalyzeAccepted,
    CarrierMessageCreate,
    CarrierMessageOut,
    ExceptionAnalyze,
    ExceptionClearIssue,
    ExceptionClose,
    ExceptionConfirm,
    ExceptionCreate,
    ExceptionOut,
    ExceptionPage,
    ExceptionPatch,
    ExceptionResolve,
    ExceptionTimelinePage,
    MessageAccepted,
)
from app.services.exceptions import ExceptionService, search_exceptions
from app.services.serializers import exception_brief

router = APIRouter(prefix="/exceptions", tags=["exceptions"])

ExceptionView = Annotated[RequestContext, Depends(require(Perm.EXCEPTION_VIEW))]
ExceptionHandle = Annotated[RequestContext, Depends(require(Perm.EXCEPTION_HANDLE))]
ExceptionCreatePerm = Annotated[RequestContext, Depends(require(Perm.EXCEPTION_CREATE))]
ExceptionForceClose = Annotated[RequestContext, Depends(require(Perm.EXCEPTION_FORCE_CLOSE))]

SORT_FIELDS = {
    "id",
    "risk_score",
    "level",
    "status",
    "created_at",
    "updated_at",
    "occurred_at",
    "expected_eta_at",
    "sla_delay_minutes",
    "merged_count",
}
DEFAULT_SORT = ["-risk_score", "-id"]


def parse_sort(sort: str | None, allowed: set[str], default: list[str]) -> list[Any]:
    from app.models.exception import ExceptionCase

    tokens = [token.strip() for token in (sort or "").split(",") if token.strip()]
    if not tokens:
        tokens = list(default)
    columns: list[Any] = []
    for token in tokens:
        descending = token.startswith("-")
        field = token.lstrip("-+")
        if field not in allowed:
            raise validation_error("排序字段非法", fields=[{"loc": "sort", "msg": field}])
        column = getattr(ExceptionCase, field)
        columns.append(column.desc() if descending else column.asc())
    return columns


@router.get("", response_model=ExceptionPage, summary="异常列表（默认 risk_score desc，关键字支持单号/说明）")
def list_exceptions(
    ctx: ExceptionView,
    page: PageDep,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    level: str | None = None,
    type: Annotated[str | None, Query(alias="type")] = None,
    customer_id: int | None = None,
    sla_breached: bool | None = None,
    assigned_to: int | None = None,
    q: str | None = None,
    keyword: str | None = None,
    sort: str | None = None,
) -> Any:
    items, total = search_exceptions(
        ctx.repos,
        status=status_filter,
        level=level,
        type_=type,
        customer_id=customer_id,
        sla_breached=sla_breached,
        assigned_to=assigned_to,
        keyword=q or keyword,
        order_by=parse_sort(sort, SORT_FIELDS, DEFAULT_SORT),
        page=page.page,
        page_size=page.page_size,
    )
    return {
        "items": [exception_brief(ctx.repos, case) for case in items],
        "total": total,
        "page": page.page,
        "page_size": page.page_size,
    }


@router.post(
    "",
    response_model=ExceptionOut,
    status_code=status.HTTP_201_CREATED,
    summary="手工建单（MANUAL；指定 level 仅 ADMIN+）",
)
def create_exception(ctx: ExceptionCreatePerm, payload: ExceptionCreate) -> Any:
    if payload.level is not None and not has_perm(ctx.role, Perm.EXCEPTION_FORCE_CLOSE):
        raise perm_denied("仅 ADMIN+ 可以在手工建单时指定风险等级", required="ADMIN")
    case = ExceptionService(ctx.repos).create_manual(
        order_id=payload.order_id,
        type=payload.type,
        occurred_at=payload.occurred_at,
        note=payload.note,
        level=payload.level,
        actor_id=ctx.user.id,
    )
    return exception_brief(ctx.repos, case)


@router.get("/{exception_id}", response_model=ExceptionOut, summary="异常详情（主单 + 订单/客户/车辆/SLA 快照）")
def get_exception(ctx: ExceptionView, exception_id: int) -> Any:
    return ExceptionService(ctx.repos).detail(exception_id)


@router.patch("/{exception_id}", response_model=ExceptionOut, summary="改 assigned_to / remark（不改 status）")
def patch_exception(ctx: ExceptionHandle, exception_id: int, payload: ExceptionPatch) -> Any:
    service = ExceptionService(ctx.repos)
    case = service.update_basic(
        exception_id,
        expected_version=payload.expected_version,
        actor_id=ctx.user.id,
        assigned_to=payload.assigned_to,
        remark=payload.remark,
    )
    return exception_brief(ctx.repos, case)


@router.post(
    "/{exception_id}/confirm",
    response_model=ExceptionOut,
    summary="DETECTED → PROCESSING（确认即进入处理中）",
)
def confirm_exception(
    ctx: ExceptionHandle,
    exception_id: int,
    payload: ExceptionConfirm | None = None,
) -> Any:
    service = ExceptionService(ctx.repos)
    case = service.confirm(
        exception_id,
        expected_version=payload.expected_version if payload else None,
        actor_id=ctx.user.id,
        note=payload.note if payload else None,
    )
    return exception_brief(ctx.repos, case)


@router.post(
    "/{exception_id}/analyze",
    response_model=AnalyzeAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="在「处理中」发起 AI 分析，创建 ai_analysis（复用命中时 200；分析期间异常状态不变）",
)
def analyze_exception(
    ctx: ExceptionHandle,
    exception_id: int,
    response: Response,
    payload: ExceptionAnalyze | None = None,
) -> Any:
    result = ExceptionService(ctx.repos).request_analysis(
        exception_id,
        expected_version=payload.expected_version if payload else None,
        actor_id=ctx.user.id,
    )
    if result.get("reused"):
        response.status_code = status.HTTP_200_OK
    return result


@router.post(
    "/{exception_id}/clear-vehicle-issue",
    response_model=ExceptionOut,
    summary="车辆已修复：只解除「车辆故障」问题，异常单继续（不是结束整单）",
)
def clear_vehicle_issue(
    ctx: ExceptionHandle,
    exception_id: int,
    payload: ExceptionClearIssue,
) -> Any:
    """信号级闭环：把"车辆故障"这条问题从风险因子里去掉（车辆状态恢复），异常状态不变。

    与 `/resolve` 的区别：resolve 是整单结束；本接口只解除其中一个问题，
    延误 / SLA 违约等其它因子继续计分（用户口径："我想要的只是把车辆故障那 1 分去掉，异常仍然存在"）。
    """
    service = ExceptionService(ctx.repos)
    case = service.clear_vehicle_issue(
        exception_id,
        note=payload.note,
        expected_version=payload.expected_version,
        actor_id=ctx.user.id,
    )
    return exception_brief(ctx.repos, case)


@router.post("/{exception_id}/resolve", response_model=ExceptionOut, summary="→ RESOLVED（必填 note）")
def resolve_exception(ctx: ExceptionHandle, exception_id: int, payload: ExceptionResolve) -> Any:
    service = ExceptionService(ctx.repos)
    case = service.resolve(
        exception_id,
        note=payload.note,
        expected_version=payload.expected_version,
        actor_id=ctx.user.id,
    )
    return exception_brief(ctx.repos, case)


@router.post("/{exception_id}/close", response_model=ExceptionOut, summary="→ CLOSED（TERMINAL，不可逆）")
def close_exception(ctx: ExceptionHandle, exception_id: int, payload: ExceptionClose) -> Any:
    service = ExceptionService(ctx.repos)
    forced = has_perm(ctx.role, Perm.EXCEPTION_FORCE_CLOSE)
    case = service.close(
        exception_id,
        reason_code=payload.reason_code,
        note=payload.note,
        expected_version=payload.expected_version,
        actor_id=ctx.user.id,
        forced=forced,
    )
    return exception_brief(ctx.repos, case)


@router.get("/{exception_id}/events", response_model=ExceptionTimelinePage, summary="异常时间线（分页）")
def exception_timeline(ctx: ExceptionView, exception_id: int, page: PageDep) -> Any:
    items, total = ExceptionService(ctx.repos).timeline(
        exception_id, page=page.page, page_size=page.page_size
    )
    return {"items": items, "total": total, "page": page.page, "page_size": page.page_size}


@router.get("/{exception_id}/messages", response_model=list[CarrierMessageOut], summary="承运商消息列表")
def list_messages(ctx: ExceptionView, exception_id: int) -> Any:
    return ExceptionService(ctx.repos).list_messages(exception_id)


@router.post(
    "/{exception_id}/messages",
    response_model=MessageAccepted,
    status_code=status.HTTP_201_CREATED,
    summary="录入承运商消息 → 同步触发 AI 解析 → 重算 ETA",
)
def add_message(ctx: ExceptionHandle, exception_id: int, payload: CarrierMessageCreate) -> Any:
    return ExceptionService(ctx.repos).add_message(
        exception_id,
        raw_text=payload.raw_text,
        channel=payload.channel,
        sender_name=payload.sender_name,
        received_at=payload.received_at,
        expected_version=payload.expected_version,
        actor_id=ctx.user.id,
    )


__all__ = ["router"]
