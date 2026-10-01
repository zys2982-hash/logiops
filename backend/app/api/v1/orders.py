"""订单接口（基线文档 §10.3 【订单】/orders）。

Router 只做鉴权 require(Perm) / 参数校验 / 编排，业务全部在 OrderService。
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import PageDep, RequestContext, require, require_any
from app.core.errors import perm_denied, validation_error
from app.core.permissions import Perm, has_perm
from app.models.transport import Order
from app.schemas.exception import ExceptionOut
from app.schemas.order import OrderCreate, OrderOut, OrderPage, OrderUpdate
from app.services.common import to_naive_utc
from app.services.orders import OrderService
from app.services.serializers import exception_brief, order_out

router = APIRouter(prefix="/orders", tags=["orders"])

OrderView = Annotated[RequestContext, Depends(require(Perm.ORDER_VIEW))]
OrderManage = Annotated[RequestContext, Depends(require(Perm.ORDER_MANAGE))]
# 派车是 OPERATOR+（§8.1），改基础信息是 ADMIN+（§9.2）→ 路由放宽，处理函数内按字段收紧
OrderPatch = Annotated[RequestContext, Depends(require_any(Perm.ORDER_MANAGE, Perm.TRACKING_WRITE))]
BASIC_FIELDS = ("origin_city", "dest_city", "cargo_desc", "weight_ton", "distance_km", "remark")

SORT_FIELDS = {
    "id",
    "order_no",
    "status",
    "created_at",
    "updated_at",
    "dispatched_at",
    "promised_delivery_at",
    "current_eta_at",
    "delivered_at",
    "distance_km",
}
DEFAULT_SORT = ["-id"]


def parse_sort(sort: str | None, allowed: set[str], default: list[str]) -> list[Any]:
    """?sort=-risk_score,created_at；白名单字段，非法字段 400（§10.1）。"""
    tokens = [token.strip() for token in (sort or "").split(",") if token.strip()]
    if not tokens:
        tokens = list(default)
    columns: list[Any] = []
    for token in tokens:
        descending = token.startswith("-")
        field = token.lstrip("-+")
        if field not in allowed:
            raise validation_error("排序字段非法", fields=[{"loc": "sort", "msg": field}])
        column = getattr(Order, field)
        columns.append(column.desc() if descending else column.asc())
    return columns


@router.get("", response_model=OrderPage, summary="订单列表")
def list_orders(
    ctx: OrderView,
    page: PageDep,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    customer_id: int | None = None,
    order_no: str | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    sort: str | None = None,
) -> Any:
    items, total = ctx.repos.orders.search(
        status=status_filter,
        customer_id=customer_id,
        order_no=order_no,
        created_from=to_naive_utc(created_from),
        created_to=to_naive_utc(created_to),
        sort=parse_sort(sort, SORT_FIELDS, DEFAULT_SORT),
        page=page.page,
        page_size=page.page_size,
    )
    return {
        "items": [order_out(ctx.repos, order) for order in items],
        "total": total,
        "page": page.page,
        "page_size": page.page_size,
    }


@router.post("", response_model=OrderOut, status_code=status.HTTP_201_CREATED, summary="创建订单（CREATED）")
def create_order(ctx: OrderManage, payload: OrderCreate) -> Any:
    order = OrderService(ctx.repos).create(
        customer_id=payload.customer_id,
        origin_city=payload.origin_city,
        dest_city=payload.dest_city,
        order_no=payload.order_no,
        cargo_desc=payload.cargo_desc,
        weight_ton=payload.weight_ton,
        distance_km=payload.distance_km,
        remark=payload.remark,
        actor_id=ctx.user.id,
    )
    return order_out(ctx.repos, order, full=True)


@router.get("/{order_id}", response_model=OrderOut, summary="订单详情")
def get_order(ctx: OrderView, order_id: int) -> Any:
    order = OrderService(ctx.repos).get(order_id)
    return order_out(ctx.repos, order, full=True)


@router.patch("/{order_id}", response_model=OrderOut, summary="改基础信息 / 派车（CREATED→DISPATCHED）")
def patch_order(ctx: OrderPatch, order_id: int, payload: OrderUpdate) -> Any:
    fields = payload.model_dump(exclude_unset=True, exclude={"expected_version"})
    basic = [name for name in BASIC_FIELDS if name in fields]
    if basic and not has_perm(ctx.role, Perm.ORDER_MANAGE):
        raise perm_denied(
            "修改订单基础信息需要 ADMIN+（OPERATOR 只能派车）",
            required=str(Perm.ORDER_MANAGE),
            fields=basic,
        )
    order = OrderService(ctx.repos).update_basic(
        order_id,
        expected_version=payload.expected_version,
        actor_id=ctx.user.id,
        **fields,
    )
    return order_out(ctx.repos, order, full=True)


@router.get("/{order_id}/exceptions", response_model=list[ExceptionOut], summary="订单关联异常")
def list_order_exceptions(ctx: OrderView, order_id: int) -> Any:
    OrderService(ctx.repos).get(order_id)
    cases, _ = ctx.repos.exceptions.list(
        filters=[ctx.repos.exceptions.model.order_id == order_id],
        order_by=[ctx.repos.exceptions.model.id.desc()],
        page=1,
        page_size=50,
    )
    return [exception_brief(ctx.repos, case) for case in cases]


__all__ = ["parse_sort", "router"]
