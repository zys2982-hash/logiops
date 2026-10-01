"""订单接口（§10.3 /orders）：派车算 SLA、轨迹触发 ETA、乐观锁 409、状态机 409、VIEWER 403、跨租户 404。"""

from __future__ import annotations

from app.models.auth import Workspace
from app.models.master import Customer
from app.models.transport import Order
from app.services import read_models

ORDERS = "/api/v1/orders"


def _other_tenant_order(db_session, bootstrap) -> Order:
    workspace = Workspace(name="其他租户", code="OT", owner_user_id=bootstrap["users"]["OWNER"].id)
    db_session.add(workspace)
    db_session.flush()
    customer = Customer(workspace_id=workspace.id, code="OT-01", name="其他客户", level="NORMAL")
    db_session.add(customer)
    db_session.flush()
    order = Order(
        workspace_id=workspace.id,
        order_no="SO-OTHER-0001",
        customer_id=customer.id,
        origin_city="北京",
        dest_city="广州",
        distance_km=500,
        status="CREATED",
    )
    db_session.add(order)
    db_session.commit()
    return order


def _create_order(client, headers, bootstrap, *, customer="vip") -> dict:
    response = client.post(
        ORDERS,
        headers=headers,
        json={
            "customer_id": bootstrap["customers"][customer].id,
            "origin_city": "天津",
            "dest_city": "上海",
            "distance_km": 1200,
            "cargo_desc": "机械配件",
            "weight_ton": 12.5,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_create_order_and_list_with_filters(client, bootstrap, admin_headers):
    created = _create_order(client, admin_headers, bootstrap)
    assert created["status"] == "CREATED"
    assert created["promised_delivery_at"] is None
    assert created["customer_code"] == "VIP-01"

    listed = client.get(ORDERS, headers=admin_headers, params={"order_no": created["order_no"]})
    assert listed.status_code == 200
    body = listed.json()
    assert body["total"] == 1
    assert body["page"] == 1 and body["page_size"] == 20
    assert body["items"][0]["id"] == created["id"]

    filtered = client.get(ORDERS, headers=admin_headers, params={"status": "CREATED", "sort": "-created_at"})
    assert filtered.status_code == 200
    assert filtered.json()["total"] >= 1

    bad_sort = client.get(ORDERS, headers=admin_headers, params={"sort": "not_a_field"})
    assert bad_sort.status_code == 422
    assert bad_sort.json()["error"]["code"] == "VALIDATION_ERROR"


def test_patch_order_dispatches_and_computes_sla(client, bootstrap, admin_headers, operator_headers):
    created = _create_order(client, admin_headers, bootstrap)
    patched = client.patch(
        f"{ORDERS}/{created['id']}",
        headers=operator_headers,
        json={
            "expected_version": created["version"],
            "carrier_id": bootstrap["carrier"].id,
            "vehicle_id": bootstrap["vehicle"].id,
            "driver_id": bootstrap["driver"].id,
        },
    )
    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert body["status"] == "DISPATCHED"
    assert body["promised_delivery_at"] == read_models.iso(bootstrap["promised_vip"])
    assert body["original_eta_at"] == read_models.iso(bootstrap["promised_vip"])
    assert body["vehicle_plate"] == "津A·12345"
    assert body["sla"]["deadline_offset_hours"] == 24
    assert body["sla"]["max_delay_minutes"] == 0


def test_patch_order_optimistic_lock_conflict(client, bootstrap, admin_headers):
    created = _create_order(client, admin_headers, bootstrap)
    response = client.patch(
        f"{ORDERS}/{created['id']}",
        headers=admin_headers,
        json={"expected_version": created["version"] + 10, "remark": "并发修改"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "OPTIMISTIC_LOCK_CONFLICT"


def test_tracking_triggers_eta_and_state_machine(client, bootstrap, admin_headers, operator_headers):
    created = _create_order(client, admin_headers, bootstrap)
    client.patch(
        f"{ORDERS}/{created['id']}",
        headers=admin_headers,
        json={"carrier_id": bootstrap["carrier"].id, "vehicle_id": bootstrap["vehicle"].id},
    )

    depart = client.post(
        f"{ORDERS}/{created['id']}/tracking-events",
        headers=operator_headers,
        json={"event_type": "DEPART", "city": "天津", "source": "OPERATOR", "speed_kmh": 50},
    )
    assert depart.status_code == 201, depart.text
    body = depart.json()
    assert body["event_type"] == "DEPART"
    assert body["eta_method"] in {"MOVING_AVG_SPEED", "FALLBACK"}
    assert body["current_eta_at"] is not None

    detail = client.get(f"{ORDERS}/{created['id']}", headers=operator_headers).json()
    assert detail["status"] == "IN_TRANSIT"

    events = client.get(f"{ORDERS}/{created['id']}/tracking-events", headers=operator_headers)
    assert events.status_code == 200
    assert events.json()["total"] == 1

    delivered = client.post(
        f"{ORDERS}/{created['id']}/tracking-events",
        headers=operator_headers,
        json={"event_type": "DELIVER", "city": "上海"},
    )
    assert delivered.status_code == 201, delivered.text
    assert client.get(f"{ORDERS}/{created['id']}", headers=operator_headers).json()["status"] == "DELIVERED"

    # 终态订单不能再追加轨迹 → 409
    again = client.post(
        f"{ORDERS}/{created['id']}/tracking-events",
        headers=operator_headers,
        json={"event_type": "NOTE", "city": "上海"},
    )
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "STATE_TRANSITION_INVALID"


def test_illegal_transition_deliver_from_created(client, bootstrap, admin_headers, operator_headers):
    created = _create_order(client, admin_headers, bootstrap)
    response = client.post(
        f"{ORDERS}/{created['id']}/tracking-events",
        headers=operator_headers,
        json={"event_type": "DELIVER", "city": "上海"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "STATE_TRANSITION_INVALID"


def test_viewer_cannot_write_orders(client, bootstrap, admin_headers, viewer_headers):
    created = _create_order(client, admin_headers, bootstrap)
    assert (
        client.post(
            ORDERS,
            headers=viewer_headers,
            json={"customer_id": bootstrap["customers"]["vip"].id, "origin_city": "A", "dest_city": "B"},
        ).status_code
        == 403
    )
    assert client.patch(f"{ORDERS}/{created['id']}", headers=viewer_headers, json={"remark": "x"}).status_code == 403
    assert (
        client.post(
            f"{ORDERS}/{created['id']}/tracking-events",
            headers=viewer_headers,
            json={"event_type": "NOTE", "city": "天津"},
        ).status_code
        == 403
    )
    # 只读接口 VIEWER 可以看
    assert client.get(ORDERS, headers=viewer_headers).status_code == 200


def test_cross_tenant_order_returns_404(client, db_session, bootstrap, admin_headers):
    other = _other_tenant_order(db_session, bootstrap)
    response = client.get(f"{ORDERS}/{other.id}", headers=admin_headers)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "RESOURCE_NOT_FOUND"
    assert client.get(f"{ORDERS}/{other.id}/exceptions", headers=admin_headers).status_code == 404


def test_order_exceptions_endpoint_shape(client, bootstrap, admin_headers, operator_headers):
    created = _create_order(client, admin_headers, bootstrap)
    response = client.get(f"{ORDERS}/{created['id']}/exceptions", headers=operator_headers)
    assert response.status_code == 200
    assert response.json() == []
