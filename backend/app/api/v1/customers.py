"""客户主数据（基线文档 §10.3【主数据】、§9.2 矩阵）。

- 列表/详情全部经 ``Repos``（已注入 workspace 过滤）→ 跨租户 404。
- 写操作 require ``customer.manage``（VIEWER/OPERATOR → 403）并写审计。
- 删除是软删除语义（``is_deleted=True``，不进回收站，§3.2）。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from fastapi import status as http_status
from sqlalchemy import or_

from app.api.deps import PageDep, RequestContext, require
from app.core.errors import ErrorCode, conflict
from app.core.permissions import Perm
from app.models.master import Customer
from app.schemas.common import Page, page_of, parse_sort, patch_payload
from app.schemas.master import CustomerCreate, CustomerOut, CustomerUpdate

router = APIRouter(prefix="/customers", tags=["master-data"])

ViewCtx = Annotated[RequestContext, Depends(require(Perm.CUSTOMER_VIEW))]
ManageCtx = Annotated[RequestContext, Depends(require(Perm.CUSTOMER_MANAGE))]

SORTABLE: dict[str, Any] = {
    "id": Customer.id,
    "code": Customer.code,
    "name": Customer.name,
    "level": Customer.level,
    "status": Customer.status,
    "version": Customer.version,
    "created_at": Customer.created_at,
    "updated_at": Customer.updated_at,
}


@router.get("", response_model=Page[CustomerOut], summary="客户列表（?q&level&status&sort&page&page_size）")
def list_customers(
    ctx: ViewCtx,
    page: PageDep,
    q: Annotated[str | None, Query(max_length=64, description="按名称/编码模糊搜索")] = None,
    level: Annotated[str | None, Query(max_length=16)] = None,
    status: Annotated[str | None, Query(max_length=16)] = None,
    sort: Annotated[str | None, Query(max_length=128)] = None,
) -> Page[CustomerOut]:
    filters: list[Any] = []
    if q:
        filters.append(or_(Customer.name.like(f"%{q}%"), Customer.code.like(f"%{q}%")))
    if level:
        filters.append(Customer.level == level)
    if status:
        filters.append(Customer.status == status)
    rows, total = ctx.repos.customers.list(
        filters=filters,
        order_by=parse_sort(sort, SORTABLE, [Customer.id.desc()]),
        page=page.page,
        page_size=page.page_size,
    )
    return page_of(CustomerOut, rows, total, page.page, page.page_size)


@router.post("", response_model=CustomerOut, status_code=http_status.HTTP_201_CREATED, summary="创建客户")
def create_customer(ctx: ManageCtx, payload: CustomerCreate) -> CustomerOut:
    if ctx.repos.customers.get_by_code(payload.code) is not None:
        raise conflict(ErrorCode.DUPLICATE_ENTITY, "客户编码已存在", field="code")

    customer = Customer(
        workspace_id=ctx.workspace_id,
        code=payload.code,
        name=payload.name,
        level=str(payload.level),
        contact_name=payload.contact_name,
        contact_phone=payload.contact_phone,
        contact_email=payload.contact_email,
        notify_pref=payload.notify_pref,
        remark=payload.remark,
        status=payload.status,
    )
    ctx.repos.customers.add(customer)
    ctx.audit(
        "customer.create",
        resource_type="customer",
        resource_id=customer.id,
        after={"code": customer.code, "name": customer.name, "level": customer.level},
    )
    return CustomerOut.from_model(customer)


@router.get("/{customer_id}", response_model=CustomerOut, summary="客户详情")
def get_customer(ctx: ViewCtx, customer_id: int) -> CustomerOut:
    customer = ctx.repos.customers.get_or_404(customer_id, "客户不存在")
    return CustomerOut.from_model(customer)


@router.patch("/{customer_id}", response_model=CustomerOut, summary="修改客户（带 expected_version 时校验乐观锁）")
def update_customer(ctx: ManageCtx, customer_id: int, payload: CustomerUpdate) -> CustomerOut:
    customer = ctx.repos.customers.get_or_404(customer_id, "客户不存在")
    if payload.expected_version is not None and payload.expected_version != customer.version:
        raise conflict(
            ErrorCode.OPTIMISTIC_LOCK_CONFLICT,
            "数据已被他人更新，请刷新后重试",
            expected_version=payload.expected_version,
            current_version=customer.version,
        )

    changes = patch_payload(payload)
    before = {key: getattr(customer, key) for key in changes}
    for key, value in changes.items():
        setattr(customer, key, value)
    customer.version = int(customer.version or 1) + 1
    ctx.repos.customers.save(customer)
    ctx.audit(
        "customer.update",
        resource_type="customer",
        resource_id=customer.id,
        before=before,
        after=changes,
    )
    return CustomerOut.from_model(customer)


@router.delete("/{customer_id}", summary="删除客户（软删除）")
def delete_customer(ctx: ManageCtx, customer_id: int) -> dict:
    customer = ctx.repos.customers.get_or_404(customer_id, "客户不存在")
    ctx.repos.customers.soft_delete(customer)
    ctx.audit(
        "customer.delete",
        resource_type="customer",
        resource_id=customer.id,
        before={"is_deleted": False},
        after={"is_deleted": True},
    )
    return {"ok": True, "id": customer.id, "is_deleted": True}


__all__ = ["router"]
