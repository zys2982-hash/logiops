"""承运商主数据（基线文档 §10.3【主数据】）。写操作 require ``carrier.manage``。"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from fastapi import status as http_status
from sqlalchemy import or_

from app.api.deps import PageDep, RequestContext, require
from app.core.errors import ErrorCode, conflict
from app.core.permissions import Perm
from app.models.master import Carrier
from app.schemas.common import Page, page_of, parse_sort, patch_payload
from app.schemas.master import CarrierCreate, CarrierOut, CarrierUpdate

router = APIRouter(prefix="/carriers", tags=["master-data"])

ViewCtx = Annotated[RequestContext, Depends(require(Perm.CARRIER_VIEW))]
ManageCtx = Annotated[RequestContext, Depends(require(Perm.CARRIER_MANAGE))]

SORTABLE: dict[str, Any] = {
    "id": Carrier.id,
    "code": Carrier.code,
    "name": Carrier.name,
    "status": Carrier.status,
    "service_level": Carrier.service_level,
    "created_at": Carrier.created_at,
    "updated_at": Carrier.updated_at,
}


@router.get("", response_model=Page[CarrierOut], summary="承运商列表（?q&status&sort&page&page_size）")
def list_carriers(
    ctx: ViewCtx,
    page: PageDep,
    q: Annotated[str | None, Query(max_length=64)] = None,
    status: Annotated[str | None, Query(max_length=16)] = None,
    sort: Annotated[str | None, Query(max_length=128)] = None,
) -> Page[CarrierOut]:
    filters: list[Any] = []
    if q:
        filters.append(or_(Carrier.name.like(f"%{q}%"), Carrier.code.like(f"%{q}%")))
    if status:
        filters.append(Carrier.status == status)
    rows, total = ctx.repos.carriers.list(
        filters=filters,
        order_by=parse_sort(sort, SORTABLE, [Carrier.id.desc()]),
        page=page.page,
        page_size=page.page_size,
    )
    return page_of(CarrierOut, rows, total, page.page, page.page_size)


@router.post("", response_model=CarrierOut, status_code=http_status.HTTP_201_CREATED, summary="创建承运商")
def create_carrier(ctx: ManageCtx, payload: CarrierCreate) -> CarrierOut:
    if ctx.repos.carriers.get_by(code=payload.code) is not None:
        raise conflict(ErrorCode.DUPLICATE_ENTITY, "承运商编码已存在", field="code")

    carrier = Carrier(
        workspace_id=ctx.workspace_id,
        code=payload.code,
        name=payload.name,
        contact_name=payload.contact_name,
        contact_phone=payload.contact_phone,
        service_level=payload.service_level,
        status=str(payload.status),
        remark=payload.remark,
    )
    ctx.repos.carriers.add(carrier)
    ctx.audit(
        "carrier.create",
        resource_type="carrier",
        resource_id=carrier.id,
        after={"code": carrier.code, "name": carrier.name, "status": carrier.status},
    )
    return CarrierOut.from_model(carrier)


@router.get("/{carrier_id}", response_model=CarrierOut, summary="承运商详情")
def get_carrier(ctx: ViewCtx, carrier_id: int) -> CarrierOut:
    return CarrierOut.from_model(ctx.repos.carriers.get_or_404(carrier_id, "承运商不存在"))


@router.patch("/{carrier_id}", response_model=CarrierOut, summary="修改承运商")
def update_carrier(ctx: ManageCtx, carrier_id: int, payload: CarrierUpdate) -> CarrierOut:
    carrier = ctx.repos.carriers.get_or_404(carrier_id, "承运商不存在")
    if payload.expected_version is not None and payload.expected_version != carrier.version:
        raise conflict(
            ErrorCode.OPTIMISTIC_LOCK_CONFLICT,
            "数据已被他人更新，请刷新后重试",
            expected_version=payload.expected_version,
            current_version=carrier.version,
        )
    changes = patch_payload(payload)
    before = {key: getattr(carrier, key) for key in changes}
    for key, value in changes.items():
        setattr(carrier, key, value)
    carrier.version = int(carrier.version or 1) + 1
    ctx.repos.carriers.save(carrier)
    ctx.audit("carrier.update", resource_type="carrier", resource_id=carrier.id, before=before, after=changes)
    return CarrierOut.from_model(carrier)


@router.delete("/{carrier_id}", summary="删除承运商（软删除）")
def delete_carrier(ctx: ManageCtx, carrier_id: int) -> dict:
    carrier = ctx.repos.carriers.get_or_404(carrier_id, "承运商不存在")
    ctx.repos.carriers.soft_delete(carrier)
    ctx.audit(
        "carrier.delete",
        resource_type="carrier",
        resource_id=carrier.id,
        before={"is_deleted": False},
        after={"is_deleted": True},
    )
    return {"ok": True, "id": carrier.id, "is_deleted": True}


__all__ = ["router"]
