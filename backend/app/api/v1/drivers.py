"""司机主数据（基线文档 §10.3【主数据】）。写操作 require ``driver.manage``。"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from fastapi import status as http_status
from sqlalchemy import or_

from app.api.deps import PageDep, RequestContext, require
from app.core.errors import ErrorCode, conflict, not_found
from app.core.permissions import Perm
from app.models.master import Driver
from app.schemas.common import Page, page_of, parse_sort, patch_payload
from app.schemas.master import DriverCreate, DriverOut, DriverUpdate

router = APIRouter(prefix="/drivers", tags=["master-data"])

ViewCtx = Annotated[RequestContext, Depends(require(Perm.DRIVER_VIEW))]
ManageCtx = Annotated[RequestContext, Depends(require(Perm.DRIVER_MANAGE))]

SORTABLE: dict[str, Any] = {
    "id": Driver.id,
    "name": Driver.name,
    "status": Driver.status,
    "carrier_id": Driver.carrier_id,
    "created_at": Driver.created_at,
    "updated_at": Driver.updated_at,
}


@router.get("", response_model=Page[DriverOut], summary="司机列表（?q&status&carrier_id&sort&page）")
def list_drivers(
    ctx: ViewCtx,
    page: PageDep,
    q: Annotated[str | None, Query(max_length=64, description="按姓名/驾照号模糊搜索")] = None,
    status: Annotated[str | None, Query(max_length=16)] = None,
    carrier_id: Annotated[int | None, Query(ge=1)] = None,
    sort: Annotated[str | None, Query(max_length=128)] = None,
) -> Page[DriverOut]:
    filters: list[Any] = []
    if q:
        filters.append(or_(Driver.name.like(f"%{q}%"), Driver.license_no.like(f"%{q}%")))
    if status:
        filters.append(Driver.status == status)
    if carrier_id:
        filters.append(Driver.carrier_id == carrier_id)
    rows, total = ctx.repos.drivers.list(
        filters=filters,
        order_by=parse_sort(sort, SORTABLE, [Driver.id.desc()]),
        page=page.page,
        page_size=page.page_size,
    )
    return page_of(DriverOut, rows, total, page.page, page.page_size)


@router.post("", response_model=DriverOut, status_code=http_status.HTTP_201_CREATED, summary="创建司机")
def create_driver(ctx: ManageCtx, payload: DriverCreate) -> DriverOut:
    if payload.carrier_id is not None and ctx.repos.carriers.get(payload.carrier_id) is None:
        raise not_found("承运商不存在", carrier_id=payload.carrier_id)

    driver = Driver(
        workspace_id=ctx.workspace_id,
        name=payload.name,
        phone=payload.phone,
        carrier_id=payload.carrier_id,
        license_no=payload.license_no,
        status=str(payload.status),
    )
    ctx.repos.drivers.add(driver)
    ctx.audit(
        "driver.create",
        resource_type="driver",
        resource_id=driver.id,
        after={"name": driver.name, "status": driver.status, "carrier_id": driver.carrier_id},
    )
    return DriverOut.from_model(driver)


@router.get("/{driver_id}", response_model=DriverOut, summary="司机详情")
def get_driver(ctx: ViewCtx, driver_id: int) -> DriverOut:
    return DriverOut.from_model(ctx.repos.drivers.get_or_404(driver_id, "司机不存在"))


@router.patch("/{driver_id}", response_model=DriverOut, summary="修改司机")
def update_driver(ctx: ManageCtx, driver_id: int, payload: DriverUpdate) -> DriverOut:
    driver = ctx.repos.drivers.get_or_404(driver_id, "司机不存在")
    if payload.expected_version is not None and payload.expected_version != driver.version:
        raise conflict(
            ErrorCode.OPTIMISTIC_LOCK_CONFLICT,
            "数据已被他人更新，请刷新后重试",
            expected_version=payload.expected_version,
            current_version=driver.version,
        )
    if payload.carrier_id is not None and ctx.repos.carriers.get(payload.carrier_id) is None:
        raise not_found("承运商不存在", carrier_id=payload.carrier_id)
    changes = patch_payload(payload)
    before = {key: getattr(driver, key) for key in changes}
    for key, value in changes.items():
        setattr(driver, key, value)
    driver.version = int(driver.version or 1) + 1
    ctx.repos.drivers.save(driver)
    ctx.audit("driver.update", resource_type="driver", resource_id=driver.id, before=before, after=changes)
    return DriverOut.from_model(driver)


@router.delete("/{driver_id}", summary="删除司机（软删除）")
def delete_driver(ctx: ManageCtx, driver_id: int) -> dict:
    driver = ctx.repos.drivers.get_or_404(driver_id, "司机不存在")
    ctx.repos.drivers.soft_delete(driver)
    ctx.audit(
        "driver.delete",
        resource_type="driver",
        resource_id=driver.id,
        before={"is_deleted": False},
        after={"is_deleted": True},
    )
    return {"ok": True, "id": driver.id, "is_deleted": True}


__all__ = ["router"]
