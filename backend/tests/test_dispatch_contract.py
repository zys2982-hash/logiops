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
