"""车辆主数据（基线文档 §10.3【主数据】）。写操作 require ``vehicle.manage``。"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from fastapi import status as http_status

from app.api.deps import PageDep, RequestContext, require
from app.core.errors import ErrorCode, conflict, not_found, validation_error
from app.core.permissions import Perm
from app.models.master import Vehicle
from app.schemas.common import Page, page_of, parse_sort, patch_payload
from app.schemas.master import VehicleCreate, VehicleOut, VehicleUpdate

router = APIRouter(prefix="/vehicles", tags=["master-data"])

ViewCtx = Annotated[RequestContext, Depends(require(Perm.VEHICLE_VIEW))]
ManageCtx = Annotated[RequestContext, Depends(require(Perm.VEHICLE_MANAGE))]

SORTABLE: dict[str, Any] = {
    "id": Vehicle.id,
    "plate_no": Vehicle.plate_no,
    "status": Vehicle.status,
    "carrier_id": Vehicle.carrier_id,
    "current_city": Vehicle.current_city,
    "created_at": Vehicle.created_at,
    "updated_at": Vehicle.updated_at,
}


def _check_relations(ctx: RequestContext, payload: Any, vehicle: Vehicle | None = None) -> None:
    carrier_id = getattr(payload, "carrier_id", None)
    if carrier_id is not None and ctx.repos.carriers.get(carrier_id) is None:
        raise not_found("承运商不存在", carrier_id=carrier_id)
    driver_id = getattr(payload, "current_driver_id", None)
    if driver_id is not None and ctx.repos.drivers.get(driver_id) is None:
        raise not_found("司机不存在", current_driver_id=driver_id)

    # 绑定一致性（项目约定：车与司机 1:1 固定绑定，不做排班/分配表；换人由车辆编辑页面维护）：
    # 主驾司机必须与车辆同属一家承运商，否则会造出"京东的车 + 德邦的司机"这种跨承运商绑定。
    # 用 model_fields_set 区分"没传该字段"与"显式清空"，避免把清空误判成沿用旧值。
    provided = getattr(payload, "model_fields_set", set())
    effective_carrier = (
        carrier_id if "carrier_id" in provided else (vehicle.carrier_id if vehicle else None)
    )
    effective_driver = (
        getattr(payload, "current_driver_id", None)
        if "current_driver_id" in provided
        else (vehicle.current_driver_id if vehicle else None)
    )
    if effective_carrier and effective_driver:
        driver = ctx.repos.drivers.get(effective_driver)
        if driver is not None and driver.carrier_id is not None and driver.carrier_id != effective_carrier:
            carrier = ctx.repos.carriers.get(effective_carrier)
            driver_carrier = ctx.repos.carriers.get(driver.carrier_id)
            raise validation_error(
                "主驾司机不属于该车辆所属承运商",
                fields=[
                    {
                        "loc": "current_driver_id",
                        "msg": f"司机 {driver.name} 属于承运商 #{driver.carrier_id}"
                        f"（{driver_carrier.name if driver_carrier else '—'}），"
                        f"与车辆承运商 #{effective_carrier}（{carrier.name if carrier else '—'}）不一致",
                    }
                ],
            )


@router.get("", response_model=Page[VehicleOut], summary="车辆列表（?plate_no&status&carrier_id&sort&page）")
def list_vehicles(
    ctx: ViewCtx,
    page: PageDep,
    plate_no: Annotated[str | None, Query(max_length=16, description="车牌模糊搜索")] = None,
    status: Annotated[str | None, Query(max_length=16)] = None,
    carrier_id: Annotated[int | None, Query(ge=1)] = None,
    driver_id: Annotated[int | None, Query(ge=1, description="当前司机")] = None,
    sort: Annotated[str | None, Query(max_length=128)] = None,
) -> Page[VehicleOut]:
    filters: list[Any] = []
    if plate_no:
        filters.append(Vehicle.plate_no.like(f"%{plate_no}%"))
    if status:
        filters.append(Vehicle.status == status)
    if carrier_id:
        filters.append(Vehicle.carrier_id == carrier_id)
    if driver_id:
        filters.append(Vehicle.current_driver_id == driver_id)
    rows, total = ctx.repos.vehicles.list(
        filters=filters,
        order_by=parse_sort(sort, SORTABLE, [Vehicle.id.desc()]),
        page=page.page,
        page_size=page.page_size,
    )
    return page_of(VehicleOut, rows, total, page.page, page.page_size)


@router.post("", response_model=VehicleOut, status_code=http_status.HTTP_201_CREATED, summary="创建车辆")
def create_vehicle(ctx: ManageCtx, payload: VehicleCreate) -> VehicleOut:
    if ctx.repos.vehicles.get_by(plate_no=payload.plate_no) is not None:
        raise conflict(ErrorCode.DUPLICATE_ENTITY, "车牌号已存在", field="plate_no")
    _check_relations(ctx, payload)

    vehicle = Vehicle(
        workspace_id=ctx.workspace_id,
        plate_no=payload.plate_no,
        vehicle_type=payload.vehicle_type,
        capacity_ton=payload.capacity_ton,
        carrier_id=payload.carrier_id,
        status=str(payload.status),
        current_driver_id=payload.current_driver_id,
        current_city=payload.current_city,
        remark=payload.remark,
    )
    ctx.repos.vehicles.add(vehicle)
    ctx.audit(
        "vehicle.create",
        resource_type="vehicle",
        resource_id=vehicle.id,
        after={"plate_no": vehicle.plate_no, "status": vehicle.status, "carrier_id": vehicle.carrier_id},
    )
    return VehicleOut.from_model(vehicle)


@router.get("/{vehicle_id}", response_model=VehicleOut, summary="车辆详情")
def get_vehicle(ctx: ViewCtx, vehicle_id: int) -> VehicleOut:
    return VehicleOut.from_model(ctx.repos.vehicles.get_or_404(vehicle_id, "车辆不存在"))


@router.patch("/{vehicle_id}", response_model=VehicleOut, summary="修改车辆（状态/司机/位置等）")
def update_vehicle(ctx: ManageCtx, vehicle_id: int, payload: VehicleUpdate) -> VehicleOut:
    vehicle = ctx.repos.vehicles.get_or_404(vehicle_id, "车辆不存在")
    if payload.expected_version is not None and payload.expected_version != vehicle.version:
        raise conflict(
            ErrorCode.OPTIMISTIC_LOCK_CONFLICT,
            "数据已被他人更新，请刷新后重试",
            expected_version=payload.expected_version,
            current_version=vehicle.version,
        )
    _check_relations(ctx, payload, vehicle)
    changes = patch_payload(payload)
    before = {key: getattr(vehicle, key) for key in changes}
    for key, value in changes.items():
        setattr(vehicle, key, value)
    vehicle.version = int(vehicle.version or 1) + 1
    ctx.repos.vehicles.save(vehicle)
    ctx.audit("vehicle.update", resource_type="vehicle", resource_id=vehicle.id, before=before, after=changes)
    return VehicleOut.from_model(vehicle)


@router.delete("/{vehicle_id}", summary="删除车辆（软删除）")
def delete_vehicle(ctx: ManageCtx, vehicle_id: int) -> dict:
    vehicle = ctx.repos.vehicles.get_or_404(vehicle_id, "车辆不存在")
    ctx.repos.vehicles.soft_delete(vehicle)
    ctx.audit(
        "vehicle.delete",
        resource_type="vehicle",
        resource_id=vehicle.id,
        before={"is_deleted": False},
        after={"is_deleted": True},
    )
    return {"ok": True, "id": vehicle.id, "is_deleted": True}


__all__ = ["router"]
