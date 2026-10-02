"""车辆主数据（基线文档 §10.3【主数据】）。写操作 require ``vehicle.manage``。"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from fastapi import status as http_status
from sqlalchemy.exc import IntegrityError

from app.api.deps import PageDep, RequestContext, require
from app.core.errors import ErrorCode, conflict, not_found, validation_error
from app.core.permissions import Perm
from app.models.master import Driver, Vehicle
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


def _resolve_driver(
    ctx: RequestContext, payload: Any, vehicle: Vehicle | None = None
) -> tuple[int | None, bool]:
    """解析并按规则校验"主驾司机"，返回 `(effective_driver_id, 是否显式提供了司机字段)`。

    支持两种输入（手输姓名为前端主用方式）：
    - `current_driver_name`：按**承运商内姓名**匹配；不存在、同名歧义、跨承运商、已被别的车绑定都会报错；
    - `current_driver_id`：兼容既有调用方（测试/脚本）。
    规则（ADR-A18）：车与司机 **1:1 强绑定**，且必须同属一家承运商。
    """
    provided = getattr(payload, "model_fields_set", set())
    carrier_provided = "carrier_id" in provided
    name_provided = "current_driver_name" in provided
    id_provided = "current_driver_id" in provided

    carrier_id = payload.carrier_id if carrier_provided else (vehicle.carrier_id if vehicle else None)
    if carrier_provided and carrier_id is not None and ctx.repos.carriers.get(carrier_id) is None:
        raise not_found("承运商不存在", carrier_id=carrier_id)

    driver_field_provided = name_provided or id_provided
    location = "current_driver_name" if name_provided else "current_driver_id"
    raw_name = getattr(payload, "current_driver_name", None)

    if name_provided:
        name = (raw_name or "").strip()
        if not name:
            # 传空串/null = 解除绑定
            return None, True
        if carrier_id is None:
            raise validation_error(
                "请先选择承运商，再填写主驾司机姓名",
                fields=[
                    {
                        "loc": "carrier_id",
                        "msg": "同名司机可能存在于不同承运商，因此填写姓名前必须先确定承运商",
                    }
                ],
            )
        carrier = ctx.repos.carriers.get(carrier_id)
        carrier_label = carrier.name if carrier else f"#{carrier_id}"
        candidates = ctx.repos.drivers.all(filters=[Driver.carrier_id == carrier_id])
        matched = [item for item in candidates if (item.name or "").strip() == name]
        if not matched:
            raise validation_error(
                "司机不存在",
                fields=[
                    {
                        "loc": location,
                        "msg": f"承运商「{carrier_label}」下没有名为「{name}」的司机；"
                        "请先在「司机」页新增该司机，或检查姓名是否写错",
                    }
                ],
            )
        if len(matched) > 1:
            raise validation_error(
                "司机姓名不唯一",
                fields=[
                    {
                        "loc": location,
                        "msg": f"承运商「{carrier_label}」下有 {len(matched)} 名司机都叫「{name}」，"
                        "请先在「司机」页改名以区分后再绑定",
                    }
                ],
            )
        driver_id: int | None = matched[0].id
    elif id_provided:
        driver_id = getattr(payload, "current_driver_id", None)
    else:
        driver_id = vehicle.current_driver_id if vehicle else None

    if driver_id is None:
        return None, driver_field_provided

    driver = ctx.repos.drivers.get(driver_id)
    if driver is None:
        raise not_found("司机不存在", current_driver_id=driver_id)

    if carrier_id and driver.carrier_id is not None and driver.carrier_id != carrier_id:
        carrier = ctx.repos.carriers.get(carrier_id)
        driver_carrier = ctx.repos.carriers.get(driver.carrier_id)
        raise validation_error(
            "主驾司机不属于该车辆所属承运商",
            fields=[
                {
                    "loc": location,
                    "msg": f"司机 {driver.name} 属于承运商 #{driver.carrier_id}"
                    f"（{driver_carrier.name if driver_carrier else '—'}），"
                    f"与车辆承运商 #{carrier_id}（{carrier.name if carrier else '—'}）不一致",
                }
            ],
        )

    # 一人一车：该司机不能已经绑定在别的车辆上
    bound_vehicles = ctx.repos.vehicles.all(filters=[Vehicle.current_driver_id == driver_id])
    others = [item for item in bound_vehicles if vehicle is None or item.id != vehicle.id]
    if others:
        raise validation_error(
            "一名司机只能绑定一台车",
            fields=[
                {
                    "loc": location,
                    "msg": f"司机「{driver.name}」已绑定车辆 {others[0].plate_no}"
                    "（一名司机只能绑定一台车）；如需换车，请先解除原车辆的绑定",
                }
            ],
        )
    return driver_id, driver_field_provided


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
    driver_id, _ = _resolve_driver(ctx, payload, None)

    vehicle = Vehicle(
        workspace_id=ctx.workspace_id,
        plate_no=payload.plate_no,
        vehicle_type=payload.vehicle_type,
        capacity_ton=payload.capacity_ton,
        carrier_id=payload.carrier_id,
        status=str(payload.status),
        current_driver_id=driver_id,
        current_city=payload.current_city,
        remark=payload.remark,
    )
    try:
        ctx.repos.vehicles.add(vehicle)
    except IntegrityError as exc:  # 唯一索引兜底（应用层校验之外的最后一道）
        ctx.session.rollback()
        raise conflict(
            ErrorCode.DUPLICATE_ENTITY,
            "该司机已被其他车辆绑定（一名司机只能绑定一台车）",
            field="current_driver_id",
        ) from exc
    ctx.audit(
        "vehicle.create",
        resource_type="vehicle",
        resource_id=vehicle.id,
        after={
            "plate_no": vehicle.plate_no,
            "status": vehicle.status,
            "carrier_id": vehicle.carrier_id,
            "current_driver_id": vehicle.current_driver_id,
        },
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
    driver_id, driver_provided = _resolve_driver(ctx, payload, vehicle)
    changes = patch_payload(payload)
    changes.pop("current_driver_name", None)  # 姓名只用于解析；落库记的是 driver_id
    if driver_provided:
        changes["current_driver_id"] = driver_id
    before = {key: getattr(vehicle, key) for key in changes}
    for key, value in changes.items():
        setattr(vehicle, key, value)
    vehicle.version = int(vehicle.version or 1) + 1
    try:
        ctx.repos.vehicles.save(vehicle)
    except IntegrityError as exc:  # 唯一索引兜底
        ctx.session.rollback()
        raise conflict(
            ErrorCode.DUPLICATE_ENTITY,
            "该司机已被其他车辆绑定（一名司机只能绑定一台车）",
            field="current_driver_id",
        ) from exc
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
