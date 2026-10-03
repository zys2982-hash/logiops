"""车辆已修复 = **信号级闭环**：只去掉「车辆故障」那 1 分，异常单继续存在。

用户口径（原话）：「当我在这个异常对应的运输订单里结束了车辆故障的异常以后，这里直接显示异常已解决，
我想要的只是将 4 分中车辆故障的 1 分去除，变成 3 分，该异常仍然存在」。

覆盖：
- 调用后异常状态不变（仍 PROCESSING），因子里的 VEHICLE_BREAKDOWN 消失、分数正好 -1；
- 车辆状态恢复为录入时记录的原状态；
- 写 ISSUE_CLEARED 事件（信号解除）与 exception.vehicle_issue_cleared 审计；
- 详情接口的 risk_explanation 里不再有该因子；
- 终态异常 / 非车辆故障异常 → 409；重复调用幂等（仍 200，因子保持消失）。
"""

from __future__ import annotations

from sqlalchemy import select

from app.models import AuditLog
from app.models.exception import ExceptionCase
from app.models.master import Vehicle

ORDERS = "/api/v1/orders"
EXCEPTIONS = "/api/v1/exceptions"


def _order_with_vehicle(client, headers, bootstrap, *, admin_headers=None, customer: str = "vip") -> dict:
    created = client.post(
        ORDERS,
        headers=admin_headers or headers,
        json={
            "customer_id": bootstrap["customers"][customer].id,
            "origin_city": "天津",
            "dest_city": "上海",
            "distance_km": 800,
        },
    ).json()
    patched = client.patch(
        f"{ORDERS}/{created['id']}",
        headers=headers,
        json={"carrier_id": bootstrap["carrier"].id, "vehicle_id": bootstrap["vehicle"].id},
    )
    assert patched.status_code == 200, patched.text
    return created


def _breakdown_case(client, headers, bootstrap, order_id: int, *, delay_minutes: int | None) -> dict:
    payload: dict = {
        "order_id": order_id,
        "type": "VEHICLE_BREAKDOWN",
        "occurred_at": bootstrap["base_time"].isoformat(),
        "note": "车辆在济南爆胎，已联系修理厂",
    }
    if delay_minutes is not None:
        payload["delay_minutes"] = delay_minutes
    response = client.post(EXCEPTIONS, headers=headers, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _codes(payload: dict) -> list[str]:
    return [str(factor.get("code")) for factor in (payload.get("risk_factors") or [])]


def _fresh(db_session, model, obj_id: int):
    db_session.commit()
    db_session.expire_all()
    return db_session.get(model, obj_id)


def test_clear_vehicle_issue_drops_one_point_and_keeps_case_open(
    client, db_session, bootstrap, operator_headers, admin_headers
):
    """NORMAL 客户 + 延误 25min：分数 = 延误(1) + 车辆故障(1) = 2 → 解除后 = 1（看得见的 -1）。"""
    order = _order_with_vehicle(
        client, operator_headers, bootstrap, admin_headers=admin_headers, customer="normal"
    )
    case = _breakdown_case(client, operator_headers, bootstrap, order["id"], delay_minutes=25)
    assert "VEHICLE_BREAKDOWN" in _codes(case), case["risk_factors"]
    score_before = int(case["risk_score"])
    assert score_before == 2, case["risk_factors"]
    assert _fresh(db_session, Vehicle, bootstrap["vehicle"].id).status == "REPAIRING"

    # 推进一步到「处理中」，更接近用户实际操作时的状态
    confirmed = client.post(
        f"{EXCEPTIONS}/{case['id']}/confirm",
        headers=operator_headers,
        json={"expected_version": case["version"]},
    )
    assert confirmed.status_code == 200, confirmed.text
    status_before = confirmed.json()["status"]
    assert status_before == "PROCESSING"

    cleared = client.post(
        f"{EXCEPTIONS}/{case['id']}/clear-vehicle-issue",
        headers=operator_headers,
        json={"expected_version": confirmed.json()["version"], "note": "轮胎已更换，车辆恢复在途"},
    )
    assert cleared.status_code == 200, cleared.text
    body = cleared.json()

    assert body["status"] == "PROCESSING", "解除车辆故障不等于结束异常（用户口径）"
    assert "VEHICLE_BREAKDOWN" not in _codes(body), body["risk_factors"]
    assert int(body["risk_score"]) == score_before - 1, (score_before, body["risk_score"])
    assert _fresh(db_session, Vehicle, bootstrap["vehicle"].id).status == "IN_TRANSIT", "车辆状态要恢复原值"
    assert _fresh(db_session, ExceptionCase, case["id"]).vehicle_status_before is None

    # 详情接口的可解释性里也不再有该因子
    detail = client.get(f"{EXCEPTIONS}/{case['id']}", headers=operator_headers).json()
    codes = [factor["code"] for factor in detail["risk_explanation"]["factors"]]
    assert "VEHICLE_BREAKDOWN" not in codes, codes

    events = client.get(f"{EXCEPTIONS}/{case['id']}/events", headers=operator_headers).json()["items"]
    assert any(event["event_type"] == "ISSUE_CLEARED" for event in events), events
    audits = list(
        db_session.scalars(
            select(AuditLog).where(
                AuditLog.action == "exception.vehicle_issue_cleared",
                AuditLog.resource_id == case["id"],
            )
        ).all()
    )
    assert audits, "必须留审计"

    # 幂等：再点一次仍 200，且因子保持消失、状态不变
    again = client.post(
        f"{EXCEPTIONS}/{case['id']}/clear-vehicle-issue",
        headers=operator_headers,
        json={"expected_version": body["version"], "note": "重复确认"},
    )
    assert again.status_code == 200, again.text
    assert "VEHICLE_BREAKDOWN" not in _codes(again.json())
    assert again.json()["status"] == "PROCESSING"


def test_repair_end_tracking_removes_factor_automatically(
    client, db_session, bootstrap, operator_headers, admin_headers
):
    """订单侧录「维修完成（REPAIR_END）」→ 车辆改回在途 → 异常读取时因子**自动移除**（不用点任何按钮）。

    用户口径：「订单那里的异常修复了以后，这里直接把汽车故障的那一分移除就可以呀」——
    所以自动路径必须成立：事实变了（车修好），因子随读取自愈。
    """
    order = _order_with_vehicle(
        client, operator_headers, bootstrap, admin_headers=admin_headers, customer="normal"
    )
    case = _breakdown_case(client, operator_headers, bootstrap, order["id"], delay_minutes=25)
    assert "VEHICLE_BREAKDOWN" in _codes(case)

    tracked = client.post(
        f"{ORDERS}/{order['id']}/tracking-events",
        headers=operator_headers,
        json={
            "event_type": "REPAIR_END",
            "city": "济南",
            "speed_kmh": 40,
            "source": "OPERATOR",
            "occurred_at": bootstrap["base_time"].isoformat(),
        },
    )
    assert tracked.status_code in (200, 201), tracked.text
    assert _fresh(db_session, Vehicle, bootstrap["vehicle"].id).status == "IN_TRANSIT", "维修完成 → 车辆回在途"

    # 没有调用 clear-vehicle-issue：读取异常详情时按"车辆现状"自动去掉该因子
    detail = client.get(f"{EXCEPTIONS}/{case['id']}", headers=operator_headers).json()
    assert "VEHICLE_BREAKDOWN" not in _codes(detail), detail["risk_factors"]
    assert detail["status"] == "DETECTED", "异常单不受影响，仍在待确认"
    stored = _fresh(db_session, ExceptionCase, case["id"])
    assert "VEHICLE_BREAKDOWN" not in [f.get("code") for f in (stored.risk_factors_json or [])], "自愈要落库"


def test_capped_score_may_not_drop_but_factor_disappears(
    client, db_session, bootstrap, operator_headers, admin_headers
):
    """封顶口径：VIP + 延误 300min 时原始分 = 延误2 + VIP1 + 车辆1 + 违约1 = 5 → 封顶 4。

    解除车辆故障后原始分 4：因子确实消失了，但**显示分数仍是 4**（min(4, 4)）——
    这是 rules/risk.py 的封顶规则，不是解除动作没生效；等级也仍是 CRITICAL。
    """
    order = _order_with_vehicle(client, operator_headers, bootstrap, admin_headers=admin_headers)
    case = _breakdown_case(client, operator_headers, bootstrap, order["id"], delay_minutes=300)
    assert int(case["risk_score"]) == 4 and case["level"] == "CRITICAL"

    cleared = client.post(
        f"{EXCEPTIONS}/{case['id']}/clear-vehicle-issue",
        headers=operator_headers,
        json={"expected_version": case["version"], "note": "轮胎已更换"},
    )
    assert cleared.status_code == 200, cleared.text
    body = cleared.json()
    assert "VEHICLE_BREAKDOWN" not in _codes(body), body["risk_factors"]
    assert int(body["risk_score"]) == 4, "原始分仍 ≥4，封顶后显示 4（封顶规则所致）"
    assert body["level"] == "CRITICAL"
    assert body["status"] == "DETECTED", "异常单仍需人工推进（待确认）"


def test_clear_vehicle_issue_rejects_terminal_and_other_types(
    client, db_session, bootstrap, operator_headers, admin_headers
):
    order = _order_with_vehicle(client, operator_headers, bootstrap, admin_headers=admin_headers)
    breakdown = _breakdown_case(client, operator_headers, bootstrap, order["id"], delay_minutes=300)

    # 非车辆故障类型 → 409
    other_order = _order_with_vehicle(client, operator_headers, bootstrap, admin_headers=admin_headers)
    other = client.post(
        EXCEPTIONS,
        headers=operator_headers,
        json={
            "order_id": other_order["id"],
            "type": "DELAY_RISK",
            "occurred_at": bootstrap["base_time"].isoformat(),
            "note": "拥堵延误",
        },
    ).json()
    rejected = client.post(
        f"{EXCEPTIONS}/{other['id']}/clear-vehicle-issue",
        headers=operator_headers,
        json={"expected_version": other["version"], "note": "误点"},
    )
    assert rejected.status_code == 409, rejected.text

    # 已关闭（终态）→ 409
    closed = client.post(
        f"{EXCEPTIONS}/{breakdown['id']}/close",
        headers=admin_headers,
        json={"reason_code": "MANUAL", "note": "归档", "expected_version": breakdown["version"]},
    )
    assert closed.status_code == 200, closed.text
    terminal = client.post(
        f"{EXCEPTIONS}/{breakdown['id']}/clear-vehicle-issue",
        headers=operator_headers,
        json={"expected_version": closed.json()["version"], "note": "已结束还想解除"},
    )
    assert terminal.status_code == 409, terminal.text
    assert terminal.json()["error"]["code"] == "STATE_TRANSITION_INVALID"
