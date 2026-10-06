"""车 / 司机状态必须随订单状态走（用户口径 2026-10-06）。

用户原话：「司机，车辆的在途状态随运输订单状态进行更新」。

规则（`app/services/crew.py`）：
- 订单在途（DISPATCHED / IN_TRANSIT）→ 车 `IN_TRANSIT`、司机 `ON_TRIP`；
- 订单结束（DELIVERED / CLOSED / CANCELLED）→ 车回 `IDLE`、司机回 `AVAILABLE`；
- 两个例外不动：车 `REPAIRING`（异常单把着）、司机 `OFF_DUTY`（停班）。

覆盖历史上漏掉的四条路径（车辆跟了、司机没跟的原因）：
a) 派车 CREATED→DISPATCHED：车与司机都要上岗；
b) **改派**（已派车后换车 / 只改司机）：旧车旧司机必须放回空闲，不能卡在「出车中」；
c) 送达：车与司机都释放；
d) 演示页「状态直设」：设成在途要上岗、设成送达/关闭要释放（原来只做释放）；
e) `crew.sync_with_orders` 全量对齐：把"与订单对不上"的历史数据修回来。
"""

from __future__ import annotations

from sqlalchemy import update

from app.models.master import Driver, Vehicle
from app.repositories import Repos
from app.services import crew

ORDERS = "/api/v1/orders"
DRIVERS = "/api/v1/drivers"
VEHICLES = "/api/v1/vehicles"
DEMO = "/api/v1/demo"


def _new_order(client, admin_headers, bootstrap) -> dict:
    response = client.post(
        ORDERS,
        headers=admin_headers,
        json={
            "customer_id": bootstrap["customers"]["normal"].id,
            "origin_city": "天津",
            "dest_city": "上海",
            "distance_km": 800,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _extra_crew(db_session, bootstrap, *, driver_name: str, plate_no: str) -> tuple[Driver, Vehicle]:
    """同一承运商下再建一台车 + 一名司机（改派用）。"""
    driver = Driver(
        workspace_id=bootstrap["workspace_id"],
        name=driver_name,
        phone="13700009999",
        carrier_id=bootstrap["carrier"].id,
        license_no="A2999999",
    )
    db_session.add(driver)
    db_session.flush()
    vehicle = Vehicle(
        workspace_id=bootstrap["workspace_id"],
        plate_no=plate_no,
        vehicle_type="9.6米厢车",
        capacity_ton=18,
        carrier_id=bootstrap["carrier"].id,
        status="IDLE",
        current_city="天津",
    )
    db_session.add(vehicle)
    db_session.flush()
    vehicle.current_driver_id = driver.id
    db_session.commit()
    return driver, vehicle


def _driver_status(client, headers, driver_id: int) -> str:
    return str(client.get(f"{DRIVERS}/{driver_id}", headers=headers).json()["status"])


def _vehicle_status(client, headers, vehicle_id: int) -> str:
    return str(client.get(f"{VEHICLES}/{vehicle_id}", headers=headers).json()["status"])


def test_dispatch_puts_vehicle_and_driver_on_trip(
    client, db_session, bootstrap, admin_headers, operator_headers
):
    """a) 派车：车回在途、司机出车。"""
    order = _new_order(client, admin_headers, bootstrap)
    assert _vehicle_status(client, operator_headers, bootstrap["vehicle"].id) == "IN_TRANSIT"
    assert _driver_status(client, operator_headers, bootstrap["driver"].id) == "AVAILABLE"

    patched = client.patch(
        f"{ORDERS}/{order['id']}",
        headers=operator_headers,
        json={"carrier_id": bootstrap["carrier"].id, "vehicle_id": bootstrap["vehicle"].id},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["status"] == "DISPATCHED"
    assert _driver_status(client, operator_headers, bootstrap["driver"].id) == "ON_TRIP"
    assert _vehicle_status(client, operator_headers, bootstrap["vehicle"].id) == "IN_TRANSIT"


def test_redispatch_releases_previous_driver_and_vehicle(
    client, db_session, bootstrap, admin_headers, operator_headers
):
    """b) 改派：新车新司机上岗，**旧车旧司机放回空闲**（不能卡在出车中）。"""
    order = _new_order(client, admin_headers, bootstrap)
    patched = client.patch(
        f"{ORDERS}/{order['id']}",
        headers=operator_headers,
        json={"carrier_id": bootstrap["carrier"].id, "vehicle_id": bootstrap["vehicle"].id},
    )
    assert patched.status_code == 200, patched.text
    assert _driver_status(client, operator_headers, bootstrap["driver"].id) == "ON_TRIP"

    other_driver, other_vehicle = _extra_crew(db_session, bootstrap, driver_name="替补司机", plate_no="津A·77777")
    reassigned = client.patch(
        f"{ORDERS}/{order['id']}",
        headers=operator_headers,
        json={
            "expected_version": patched.json()["version"],
            "vehicle_id": other_vehicle.id,
            "driver_id": other_driver.id,
        },
    )
    assert reassigned.status_code == 200, reassigned.text

    assert _driver_status(client, operator_headers, other_driver.id) == "ON_TRIP", "新司机要出车"
    assert _vehicle_status(client, operator_headers, other_vehicle.id) == "IN_TRANSIT", "新车要在途"
    assert _driver_status(client, operator_headers, bootstrap["driver"].id) == "AVAILABLE", "旧司机要放回空闲"
    assert _vehicle_status(client, operator_headers, bootstrap["vehicle"].id) == "IDLE", "旧车要放回空闲"


def test_delivered_releases_both(client, db_session, bootstrap, admin_headers, operator_headers):
    """c) 送达：车与司机都释放。"""
    order = _new_order(client, admin_headers, bootstrap)
    client.patch(
        f"{ORDERS}/{order['id']}",
        headers=operator_headers,
        json={"carrier_id": bootstrap["carrier"].id, "vehicle_id": bootstrap["vehicle"].id},
    )
    assert _driver_status(client, operator_headers, bootstrap["driver"].id) == "ON_TRIP"

    delivered = client.post(
        f"{DEMO}/actions/set-order-status",
        headers=admin_headers,
        json={"order_id": order["id"], "status": "DELIVERED", "note": "演示：直接送达"},
    )
    assert delivered.status_code == 200, delivered.text
    assert _driver_status(client, operator_headers, bootstrap["driver"].id) == "AVAILABLE"
    assert _vehicle_status(client, operator_headers, bootstrap["vehicle"].id) == "IDLE"


def test_force_status_on_trip_puts_crew_on_duty(
    client, db_session, bootstrap, admin_headers, operator_headers
):
    """d) 状态直设成在途：车与司机必须上岗（原来只处理"送达/关闭时释放"）。"""
    order = _new_order(client, admin_headers, bootstrap)
    client.patch(
        f"{ORDERS}/{order['id']}",
        headers=operator_headers,
        json={"carrier_id": bootstrap["carrier"].id, "vehicle_id": bootstrap["vehicle"].id},
    )
    # 先直设回「待发车」再直设成「在途」：模拟演示页来回拨状态
    reverted = client.post(
        f"{DEMO}/actions/set-order-status",
        headers=admin_headers,
        json={"order_id": order["id"], "status": "CREATED", "note": "回退"},
    )
    assert reverted.status_code == 200, reverted.text

    forced = client.post(
        f"{DEMO}/actions/set-order-status",
        headers=admin_headers,
        json={"order_id": order["id"], "status": "IN_TRANSIT", "note": "直设成在途"},
    )
    assert forced.status_code == 200, forced.text
    assert _driver_status(client, operator_headers, bootstrap["driver"].id) == "ON_TRIP"
    assert _vehicle_status(client, operator_headers, bootstrap["vehicle"].id) == "IN_TRANSIT"


def test_sync_with_orders_repairs_stale_crew(
    client, db_session, bootstrap, admin_headers, operator_headers
):
    """e) 全量对齐：把"没单却出车中 / 在途单却空闲"的历史数据修回来。"""
    order = _new_order(client, admin_headers, bootstrap)
    client.patch(
        f"{ORDERS}/{order['id']}",
        headers=operator_headers,
        json={"carrier_id": bootstrap["carrier"].id, "vehicle_id": bootstrap["vehicle"].id},
    )

    # 人为制造两种脏状态：在途单的司机变空闲 / 车变空闲，另造一个"没单却出车中"的司机
    stale_driver, _ = _extra_crew(db_session, bootstrap, driver_name="闲人司机", plate_no="津A·88888")
    db_session.execute(
        update(Driver)
        .where(Driver.id.in_([bootstrap["driver"].id, stale_driver.id]))
        .values(status="AVAILABLE")
    )
    db_session.execute(
        update(Driver).where(Driver.id == stale_driver.id).values(status="ON_TRIP")
    )
    db_session.execute(
        update(Vehicle).where(Vehicle.id == bootstrap["vehicle"].id).values(status="IDLE")
    )
    db_session.commit()
    db_session.expire_all()

    changes = crew.sync_with_orders(Repos(db_session, workspace_id=bootstrap["workspace_id"]))
    db_session.commit()
    by_name = {(c["kind"], c["name"]): (c["from"], c["to"]) for c in changes}

    assert ("driver", "李四") in by_name, changes  # 在途单的司机被叫回岗
    assert by_name[("driver", "李四")][1] == "ON_TRIP"
    assert ("driver", "闲人司机") in by_name  # 没单却出车中的被放回空闲
    assert by_name[("driver", "闲人司机")][1] == "AVAILABLE"
    assert ("vehicle", "津A·12345") in by_name  # 在途单的车被叫回在途
    assert by_name[("vehicle", "津A·12345")][1] == "IN_TRANSIT"

    assert _driver_status(client, operator_headers, bootstrap["driver"].id) == "ON_TRIP"
    assert _vehicle_status(client, operator_headers, bootstrap["vehicle"].id) == "IN_TRANSIT"
    assert _driver_status(client, operator_headers, stale_driver.id) == "AVAILABLE"

    # 再跑一次应为"本来就一致"
    assert crew.sync_with_orders(Repos(db_session, workspace_id=bootstrap["workspace_id"])) == []
