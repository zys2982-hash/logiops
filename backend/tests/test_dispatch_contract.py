"""派车契约测试（Lead 维护）：承运商 / 车辆 / 司机 三者必须自洽。

业务规则：**承运商是合同与追责主体，车辆与司机必须属于所选承运商**。
前端已做"先选承运商 → 再选它名下的车/司机"的级联，但直接调 API 也必须被拦住，
否则会造出"承运商 A + 承运商 B 的车"这种矛盾数据，SLA、追责与结算都会跟着错。
"""

from __future__ import annotations

from app.models.master import Carrier, Driver, Vehicle


def _create_order(client, admin_headers, customer_id: int) -> dict:
    """建单需要 order.manage（ADMIN+）；派车 OPERATOR 就能做——权限矩阵 §9.2。"""
    response = client.post(
        "/api/v1/orders",
        headers=admin_headers,
        json={"customer_id": customer_id, "origin_city": "天津", "dest_city": "上海", "distance_km": 800},
    )
    assert response.status_code in (200, 201), response.text
    return response.json()


def _other_carrier(db_session, workspace_id: int) -> tuple[Carrier, Vehicle, Driver]:
    """另建一个承运商及其名下的车与司机，用于制造"跨承运商"组合。"""
    carrier = Carrier(workspace_id=workspace_id, code="CR-99", name="外部承运商")
    db_session.add(carrier)
    db_session.flush()
    vehicle = Vehicle(workspace_id=workspace_id, plate_no="京Z·00001", carrier_id=carrier.id, status="IDLE")
    driver = Driver(workspace_id=workspace_id, name="外聘司机", carrier_id=carrier.id, status="AVAILABLE")
    db_session.add_all([vehicle, driver])
    db_session.commit()
    return carrier, vehicle, driver


def test_dispatch_rejects_vehicle_from_other_carrier(client, db_session, bootstrap, admin_headers, operator_headers):
    order = _create_order(client, admin_headers, bootstrap["customers"]["vip"].id)
    _, stranger_vehicle, _ = _other_carrier(db_session, bootstrap["workspace_id"])

    response = client.patch(
        f"/api/v1/orders/{order['id']}",
        headers=operator_headers,
        json={
            "expected_version": order["version"],
            "carrier_id": bootstrap["carrier"].id,
            "vehicle_id": stranger_vehicle.id,
        },
    )
    assert response.status_code == 422, response.text
    body = response.json()["error"]
    assert body["code"] == "VALIDATION_ERROR"
    assert any("vehicle_id" in field["loc"] for field in body["details"]["fields"])

    # 订单不能被改成矛盾状态
    after = client.get(f"/api/v1/orders/{order['id']}", headers=operator_headers).json()
    assert after["status"] == "CREATED"
    assert after["vehicle_id"] is None


def test_dispatch_rejects_driver_from_other_carrier(client, db_session, bootstrap, admin_headers, operator_headers):
    order = _create_order(client, admin_headers, bootstrap["customers"]["normal"].id)
    _, _, stranger_driver = _other_carrier(db_session, bootstrap["workspace_id"])

    response = client.patch(
        f"/api/v1/orders/{order['id']}",
        headers=operator_headers,
        json={
            "expected_version": order["version"],
            "carrier_id": bootstrap["carrier"].id,
            "vehicle_id": bootstrap["vehicle"].id,
            "driver_id": stranger_driver.id,
        },
    )
    assert response.status_code == 422, response.text
    assert any("driver_id" in field["loc"] for field in response.json()["error"]["details"]["fields"])


def test_dispatch_accepts_consistent_triple(client, db_session, bootstrap, admin_headers, operator_headers):
    """同承运商的车 + 司机 → 派车成功，三个字段都落库。"""
    order = _create_order(client, admin_headers, bootstrap["customers"]["vip"].id)

    response = client.patch(
        f"/api/v1/orders/{order['id']}",
        headers=operator_headers,
        json={
            "expected_version": order["version"],
            "carrier_id": bootstrap["carrier"].id,
            "vehicle_id": bootstrap["vehicle"].id,
            "driver_id": bootstrap["driver"].id,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "DISPATCHED"
    assert body["carrier_id"] == bootstrap["carrier"].id
    assert body["vehicle_id"] == bootstrap["vehicle"].id
    assert body["driver_id"] == bootstrap["driver"].id
    # 承诺时间在派车时按 SLA 规则算出（这票是 VIP → 24h）
    assert body["promised_delivery_at"] is not None


# --- 强绑定（ADR-A18）：车辆在运营时由其固定主驾驾驶 ---------------------------


def _other_driver_of_same_carrier(db_session, bootstrap) -> int:
    """同承运商、但不是这台车固定主驾的另一个司机。"""
    driver = Driver(
        workspace_id=bootstrap["workspace_id"],
        name="同公司另一名司机",
        carrier_id=bootstrap["carrier"].id,
        status="AVAILABLE",
    )
    db_session.add(driver)
    db_session.commit()
    return driver.id


def _unbound_vehicle(client, admin_headers, bootstrap, plate_no: str = "京Z·70001") -> dict:
    """造一台未绑定主驾的车（接口允许 current_driver_id 为空）。"""
    response = client.post(
        "/api/v1/vehicles",
        headers=admin_headers,
        json={"plate_no": plate_no, "carrier_id": bootstrap["carrier"].id, "status": "IDLE"},
    )
    assert response.status_code in (200, 201), response.text
    return response.json()


def test_dispatch_rejects_driver_that_is_not_bound_driver(
    client, db_session, bootstrap, admin_headers, operator_headers
):
    """车辆有固定主驾时，换成同承运商的另一名司机也不行（强绑定）。"""
    order = _create_order(client, admin_headers, bootstrap["customers"]["normal"].id)
    other_driver_id = _other_driver_of_same_carrier(db_session, bootstrap)

    response = client.patch(
        f"/api/v1/orders/{order['id']}",
        headers=operator_headers,
        json={
            "expected_version": order["version"],
            "carrier_id": bootstrap["carrier"].id,
            "vehicle_id": bootstrap["vehicle"].id,
            "driver_id": other_driver_id,
        },
    )
    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert "固定绑定" in error["message"]
    assert any("driver_id" in field["loc"] for field in error["details"]["fields"])
    assert "固定主驾" in error["details"]["fields"][0]["msg"]


def test_dispatch_auto_fills_bound_driver_when_driver_omitted(
    client, bootstrap, admin_headers, operator_headers
):
    """只给车辆（不给司机）→ 自动带入该车的固定主驾（"选了车，司机就是它"）。"""
    order = _create_order(client, admin_headers, bootstrap["customers"]["vip"].id)

    response = client.patch(
        f"/api/v1/orders/{order['id']}",
        headers=operator_headers,
        json={
            "expected_version": order["version"],
            "carrier_id": bootstrap["carrier"].id,
            "vehicle_id": bootstrap["vehicle"].id,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["driver_id"] == bootstrap["vehicle"].current_driver_id
    assert body["driver_id"] == bootstrap["driver"].id


def test_dispatch_rejects_unbound_vehicle_without_driver(client, bootstrap, admin_headers, operator_headers):
    """未绑定主驾的车辆 + 不指定司机 → 422（在用车辆必须有主驾）。"""
    vehicle = _unbound_vehicle(client, admin_headers, bootstrap)
    order = _create_order(client, admin_headers, bootstrap["customers"]["normal"].id)

    response = client.patch(
        f"/api/v1/orders/{order['id']}",
        headers=operator_headers,
        json={
            "expected_version": order["version"],
            "carrier_id": bootstrap["carrier"].id,
            "vehicle_id": vehicle["id"],
        },
    )
    assert response.status_code == 422, response.text
    assert "尚未绑定主驾" in response.json()["error"]["message"]


def test_dispatch_on_unbound_vehicle_establishes_binding(
    client, db_session, bootstrap, admin_headers, operator_headers
):
    """未绑定主驾的车辆 + 指定同承运商**且未绑定别车**的司机 → 派车成功，并自动建立绑定。"""
    vehicle = _unbound_vehicle(client, admin_headers, bootstrap, plate_no="京Z·70002")
    fresh_driver = Driver(
        workspace_id=bootstrap["workspace_id"],
        name="待绑定司机",
        carrier_id=bootstrap["carrier"].id,
        status="AVAILABLE",
    )
    db_session.add(fresh_driver)
    db_session.commit()
    order = _create_order(client, admin_headers, bootstrap["customers"]["vip"].id)

    response = client.patch(
        f"/api/v1/orders/{order['id']}",
        headers=operator_headers,
        json={
            "expected_version": order["version"],
            "carrier_id": bootstrap["carrier"].id,
            "vehicle_id": vehicle["id"],
            "driver_id": fresh_driver.id,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["driver_id"] == fresh_driver.id

    refreshed = client.get(f"/api/v1/vehicles/{vehicle['id']}", headers=admin_headers).json()
    assert refreshed["current_driver_id"] == fresh_driver.id, "派车后应自动建立车-司机绑定"


def test_dispatch_autobind_rejects_driver_already_bound(client, bootstrap, admin_headers, operator_headers):
    """未绑定主驾的车辆 + **已绑定别车**的司机 → 422（一人一车），而不是数据库唯一索引报 500。"""
    vehicle = _unbound_vehicle(client, admin_headers, bootstrap, plate_no="京Z·70003")
    order = _create_order(client, admin_headers, bootstrap["customers"]["normal"].id)

    response = client.patch(
        f"/api/v1/orders/{order['id']}",
        headers=operator_headers,
        json={
            "expected_version": order["version"],
            "carrier_id": bootstrap["carrier"].id,
            "vehicle_id": vehicle["id"],
            "driver_id": bootstrap["driver"].id,  # 该司机已绑定 bootstrap 的车辆
        },
    )
    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert "已绑定其他车辆" in error["message"]
    assert any("driver_id" in field["loc"] for field in error["details"]["fields"])
