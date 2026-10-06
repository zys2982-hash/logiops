"""车 / 司机状态随订单状态同步（用户口径 2026-10-06）。

用户原话：「司机，车辆的在途状态随运输订单状态进行更新」。

规则（两个方向都要跟）：
· 订单在途（`DISPATCHED` / `IN_TRANSIT`）→ 车 `IN_TRANSIT`、司机 `ON_TRIP`；
· 订单结束（`DELIVERED` / `CLOSED` / `CANCELLED`）→ 车回 `IDLE`、司机回 `AVAILABLE`。

两个例外**不动**（都不是订单能决定的）：
· 车 `REPAIRING`：由异常单把着，等人工点「解决 / 关闭」或录「维修完成」才释放
  （见 `app/services/exceptions.py::_release_vehicle_repairing`）；
· 司机 `OFF_DUTY`：停班是人事状态，不因订单而变。

用法：
· 运行时（派车 / 改派 / 状态直设 / 取消订单）用 `occupy` / `release`，只针对这一张订单；
· 数据生成与修库（seed / build_demo_data / scripts/sync_crew_status.py）用 `sync_with_orders`
  全量对齐，返回变更清单供打印与审计。
"""

from __future__ import annotations

from typing import Any

from app.models.enums import DriverStatus, OrderStatus, VehicleStatus
from app.models.transport import Order
from app.repositories import Repos
from app.services.common import bump_version

# 行程未结束的订单状态（车与司机必须在岗）
LIVE_ORDER_STATUSES: tuple[str, ...] = (
    str(OrderStatus.DISPATCHED),
    str(OrderStatus.IN_TRANSIT),
)


def occupy(repos: Repos, order: Order) -> None:
    """订单在途 → 车回在途、司机出车（维修中的车、非空闲的司机不动）。"""
    vehicle = repos.vehicles.get(order.vehicle_id) if order.vehicle_id else None
    if vehicle is not None and str(vehicle.status) == str(VehicleStatus.IDLE):
        vehicle.status = str(VehicleStatus.IN_TRANSIT)
        vehicle.current_city = vehicle.current_city or order.origin_city
        bump_version(vehicle)
        repos.vehicles.save(vehicle)

    driver = repos.drivers.get(order.driver_id) if order.driver_id else None
    if driver is not None and str(driver.status) == str(DriverStatus.AVAILABLE):
        driver.status = str(DriverStatus.ON_TRIP)
        bump_version(driver)
        repos.drivers.save(driver)


def release(
    repos: Repos,
    *,
    vehicle_id: int | None,
    driver_id: int | None,
    exclude_order_id: int,
) -> None:
    """订单结束（或改派换人换车）→ 释放车与司机。

    还被**别的未结束订单**占着的就不动（一台车/一个司机可能被多单引用时别误放）；
    车只从 `IN_TRANSIT` 放回 `IDLE`（`REPAIRING` / `OFFLINE` 不动），司机只从 `ON_TRIP` 放回 `AVAILABLE`
    （`OFF_DUTY` 不动）。
    """
    vehicle_busy, driver_busy = _busy_elsewhere(
        repos, vehicle_id=vehicle_id, driver_id=driver_id, exclude_order_id=exclude_order_id
    )

    vehicle = repos.vehicles.get(vehicle_id) if vehicle_id else None
    if (
        vehicle is not None
        and str(vehicle.status) == str(VehicleStatus.IN_TRANSIT)
        and not vehicle_busy
    ):
        vehicle.status = str(VehicleStatus.IDLE)
        bump_version(vehicle)
        repos.vehicles.save(vehicle)

    driver = repos.drivers.get(driver_id) if driver_id else None
    if (
        driver is not None
        and str(driver.status) == str(DriverStatus.ON_TRIP)
        and not driver_busy
    ):
        driver.status = str(DriverStatus.AVAILABLE)
        bump_version(driver)
        repos.drivers.save(driver)


def _busy_elsewhere(
    repos: Repos,
    *,
    vehicle_id: int | None,
    driver_id: int | None,
    exclude_order_id: int,
) -> tuple[bool, bool]:
    """该车 / 该司机是否还被**别的未结束订单**占着。"""
    vehicle_busy = driver_busy = False
    live = repos.orders.all(filters=[Order.status.in_(list(LIVE_ORDER_STATUSES))])
    for other in live:
        if other.id == exclude_order_id:
            continue
        if vehicle_id and other.vehicle_id == vehicle_id:
            vehicle_busy = True
        if driver_id and other.driver_id == driver_id:
            driver_busy = True
    return vehicle_busy, driver_busy


def sync_with_orders(repos: Repos) -> list[dict[str, Any]]:
    """按**订单现状**全量对齐车与司机的状态，返回变更清单（空列表 = 本来就一致）。

    修的是"状态和订单对不上"的历史数据；规则与 `occupy` / `release` 完全一致，
    只是范围覆盖整个工作区（seed / 演示数据重建 / 修库脚本共用）。
    """
    live = repos.orders.all(filters=[Order.status.in_(list(LIVE_ORDER_STATUSES))])
    live_vehicle_ids = {order.vehicle_id for order in live if order.vehicle_id}
    live_driver_ids = {order.driver_id for order in live if order.driver_id}
    live_origin_city = {order.vehicle_id: order.origin_city for order in live if order.vehicle_id}

    changes: list[dict[str, Any]] = []

    for vehicle in repos.vehicles.all():
        current = str(vehicle.status)
        if vehicle.id in live_vehicle_ids:
            if current == str(VehicleStatus.IDLE):
                vehicle.status = str(VehicleStatus.IN_TRANSIT)
                vehicle.current_city = vehicle.current_city or live_origin_city.get(vehicle.id)
                bump_version(vehicle)
                repos.vehicles.save(vehicle)
                changes.append(
                    {
                        "kind": "vehicle",
                        "id": vehicle.id,
                        "name": vehicle.plate_no,
                        "from": current,
                        "to": str(VehicleStatus.IN_TRANSIT),
                    }
                )
        elif current == str(VehicleStatus.IN_TRANSIT):
            vehicle.status = str(VehicleStatus.IDLE)
            bump_version(vehicle)
            repos.vehicles.save(vehicle)
            changes.append(
                {
                    "kind": "vehicle",
                    "id": vehicle.id,
                    "name": vehicle.plate_no,
                    "from": current,
                    "to": str(VehicleStatus.IDLE),
                }
            )

    for driver in repos.drivers.all():
        current = str(driver.status)
        if driver.id in live_driver_ids:
            if current != str(DriverStatus.ON_TRIP):
                driver.status = str(DriverStatus.ON_TRIP)
                bump_version(driver)
                repos.drivers.save(driver)
                changes.append(
                    {
                        "kind": "driver",
                        "id": driver.id,
                        "name": driver.name,
                        "from": current,
                        "to": str(DriverStatus.ON_TRIP),
                    }
                )
        elif current == str(DriverStatus.ON_TRIP):
            driver.status = str(DriverStatus.AVAILABLE)
            bump_version(driver)
            repos.drivers.save(driver)
            changes.append(
                {
                    "kind": "driver",
                    "id": driver.id,
                    "name": driver.name,
                    "from": current,
                    "to": str(DriverStatus.AVAILABLE),
                }
            )

    return changes


__all__ = ["LIVE_ORDER_STATUSES", "occupy", "release", "sync_with_orders"]
