"""演示工具：订单 / 异常状态**直设**（跳过状态机，但一切留痕）。

用户口径（原话）："这个订单和异常的状态应该让我可以自己选择"。
权限沿用 demo 路由：APP_ENV=local + DEMO_CONTROL（ADMIN+）。

覆盖：
- 订单可以往前设（IN_TRANSIT / DELIVERED 补时间字段），也可以**回退**（DELIVERED → IN_TRANSIT 清 delivered_at）；
- 异常可以从 DETECTED 直接跳到 CLOSED（补 closed_at / close_reason）并释放车辆维修状态；
- 终态也能回到 PROCESSING（清掉结束时间）—— 演示需要；
- 每次写审计（order.status_forced / exception.status_forced）与状态事件；
- 非法状态 422；无 demo.control 的角色 403。
"""

from __future__ import annotations

from sqlalchemy import select

from app.models import AuditLog
from app.models.exception import ExceptionCase
from app.models.master import Vehicle
from app.models.transport import Order

ORDERS = "/api/v1/orders"
EXCEPTIONS = "/api/v1/exceptions"
DEMO = "/api/v1/demo"


def _new_order(client, headers, bootstrap, *, admin_headers=None) -> dict:
    response = client.post(
        ORDERS,
        headers=admin_headers or headers,
        json={
            "customer_id": bootstrap["customers"]["vip"].id,
            "origin_city": "天津",
            "dest_city": "上海",
            "distance_km": 800,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _dispatch(client, headers, bootstrap, order_id: int) -> None:
    patched = client.patch(
        f"{ORDERS}/{order_id}",
        headers=headers,
        json={"carrier_id": bootstrap["carrier"].id, "vehicle_id": bootstrap["vehicle"].id},
    )
    assert patched.status_code == 200, patched.text


def _set_order_status(client, headers, order_id: int, status: str):
    return client.post(
        f"{DEMO}/actions/set-order-status",
        headers=headers,
        json={"order_id": order_id, "status": status, "note": "演示直设"},
    )


def _set_case_status(client, headers, case_id: int, status: str):
    return client.post(
        f"{DEMO}/actions/set-exception-status",
        headers=headers,
        json={"exception_id": case_id, "status": status, "note": "演示直设"},
    )


def _audits(db_session, action: str, resource_id: int) -> list[AuditLog]:
    return list(
        db_session.scalars(
            select(AuditLog).where(AuditLog.action == action, AuditLog.resource_id == resource_id)
        ).all()
    )


def _fresh(db_session, model, obj_id: int):
    db_session.commit()
    db_session.expire_all()
    return db_session.get(model, obj_id)


def test_order_status_can_be_set_forward_and_back(client, db_session, bootstrap, admin_headers):
    order = _new_order(client, admin_headers, bootstrap)
    assert order["status"] == "CREATED"

    to_transit = _set_order_status(client, admin_headers, order["id"], "IN_TRANSIT")
    assert to_transit.status_code == 200, to_transit.text
    assert to_transit.json()["status"] == "IN_TRANSIT"
    assert to_transit.json()["previous_status"] == "CREATED"
    stored = _fresh(db_session, Order, order["id"])
    assert stored.dispatched_at is not None, "进入在途要补 dispatched_at（否则时间线自相矛盾）"

    to_delivered = _set_order_status(client, admin_headers, order["id"], "DELIVERED")
    assert to_delivered.status_code == 200, to_delivered.text
    stored = _fresh(db_session, Order, order["id"])
    assert stored.delivered_at is not None
    assert stored.current_eta_at == stored.delivered_at

    # 状态机不允许 DELIVERED → IN_TRANSIT，但"演示直设"允许（用户口径：状态自己选）
    back = _set_order_status(client, admin_headers, order["id"], "IN_TRANSIT")
    assert back.status_code == 200, back.text
    stored = _fresh(db_session, Order, order["id"])
    assert stored.delivered_at is None, "回到未送达状态要清 delivered_at"

    assert _audits(db_session, "order.status_forced", order["id"]), "必须留审计"


def test_exception_status_direct_jump_releases_vehicle(
    client, db_session, bootstrap, operator_headers, admin_headers
):
    order = _new_order(client, operator_headers, bootstrap, admin_headers=admin_headers)
    _dispatch(client, operator_headers, bootstrap, order["id"])
    created = client.post(
        EXCEPTIONS,
        headers=operator_headers,
        json={
            "order_id": order["id"],
            "type": "VEHICLE_BREAKDOWN",
            "occurred_at": bootstrap["base_time"].isoformat(),
            "note": "车辆在济南爆胎",
        },
    )
    assert created.status_code == 201, created.text
    case = created.json()
    assert _fresh(db_session, Vehicle, bootstrap["vehicle"].id).status == "REPAIRING"

    jumped = _set_case_status(client, operator_headers, case["id"], "CLOSED")
    assert jumped.status_code == 200, jumped.text
    assert jumped.json()["status"] == "CLOSED"
    stored = _fresh(db_session, ExceptionCase, case["id"])
    assert stored.closed_at is not None
    assert stored.close_reason == "FORCED_CLOSE", "直设关闭要留下可辨识的关闭原因"
    assert _fresh(db_session, Vehicle, bootstrap["vehicle"].id).status == "IN_TRANSIT", "关闭要释放车辆维修状态"

    # 终态也能回到处理中（演示需要），并清掉结束时间
    reopened = _set_case_status(client, operator_headers, case["id"], "PROCESSING")
    assert reopened.status_code == 200, reopened.text
    stored = _fresh(db_session, ExceptionCase, case["id"])
    assert stored.closed_at is None and stored.close_reason is None

    events = client.get(f"{EXCEPTIONS}/{case['id']}/events", headers=operator_headers).json()["items"]
    forced = [event for event in events if (event.get("detail") or {}).get("forced")]
    assert forced, "直设要写 STATUS_CHANGED 事件并带 forced 标记"
    assert _audits(db_session, "exception.status_forced", case["id"])


def test_invalid_status_and_permission(client, db_session, bootstrap, admin_headers, viewer_headers):
    order = _new_order(client, admin_headers, bootstrap)

    bad = _set_order_status(client, admin_headers, order["id"], "FLYING")
    assert bad.status_code == 422, bad.text
    assert bad.json()["error"]["code"] == "VALIDATION_ERROR"

    denied = _set_order_status(client, viewer_headers, order["id"], "IN_TRANSIT")
    assert denied.status_code == 403, denied.text

    stored = _fresh(db_session, Order, order["id"])
    assert stored.status == "CREATED", "非法请求不得改动数据"
