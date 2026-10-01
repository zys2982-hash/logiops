"""轨迹接口（基线文档 §10.3 POST/GET /orders/{id}/tracking-events）。

写入轨迹后同步执行"ETA 重算 → 异常检测"（§8.4 触发时机），因此接口返回体里带
current_eta_at / eta_method / exception_id，前端时间线可立刻展示异常标记。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, status

from app.api.deps import PageDep, RequestContext, require
from app.core.permissions import Perm
from app.schemas.tracking import TrackingEventCreate, TrackingEventOut, TrackingPage
from app.services import read_models
from app.services.orders import OrderService
from app.services.serializers import tracking_out

router = APIRouter(tags=["tracking"])

TrackingView = Annotated[RequestContext, Depends(require(Perm.TRACKING_VIEW))]
TrackingWrite = Annotated[RequestContext, Depends(require(Perm.TRACKING_WRITE))]


@router.get(
    "/orders/{order_id}/tracking-events",
    response_model=TrackingPage,
    summary="订单轨迹列表（倒序）",
)
def list_tracking_events(ctx: TrackingView, order_id: int, page: PageDep) -> Any:
    OrderService(ctx.repos).get(order_id)
    events, total = ctx.repos.tracking.list(
        filters=[ctx.repos.tracking.model.order_id == order_id],
        order_by=[ctx.repos.tracking.model.occurred_at.desc(), ctx.repos.tracking.model.id.desc()],
        page=page.page,
        page_size=page.page_size,
    )
    return {
        "items": [tracking_out(event) for event in events],
        "total": total,
        "page": page.page,
        "page_size": page.page_size,
    }


@router.post(
    "/orders/{order_id}/tracking-events",
    response_model=TrackingEventOut,
    status_code=status.HTTP_201_CREATED,
    summary="写入轨迹（同步触发 ETA 重算与异常检测）",
)
def create_tracking_event(ctx: TrackingWrite, order_id: int, payload: TrackingEventCreate) -> Any:
    service = OrderService(ctx.repos)
    event = service.append_tracking(
        order_id,
        event_type=payload.event_type,
        city=payload.city,
        occurred_at=payload.occurred_at,
        source=payload.source,
        speed_kmh=payload.speed_kmh,
        address=payload.address,
        payload=payload.payload,
        actor_id=ctx.user.id,
    )
    order = service.get(order_id)
    case = ctx.repos.exceptions.find_open_by_order(order_id)
    return tracking_out(
        event,
        current_eta_at=read_models.iso(order.current_eta_at),
        exception_id=case.id if case else None,
    )


__all__ = ["router"]
