"""异常单的「当前问题」按风险因子实时推导，建单不再固定类型（用户口径）。

原话：「异常中心建表的时候不应该有异常类型的选择，一个异常订单的异常是实时改变的不要用类型固定他」。

口径：
- `type` = **建单原因**（历史，不随现实变化）；
- `current_type` = **当前问题**：未结束的单里，还带「车辆故障」因子 → 车辆故障；没有该因子时
  **沿用建单原因**（2026-10-06 修正：原来无条件兜底成"延误风险"，会把车已修好、还没人收口的
  车辆故障单错标成延误）；已结束的单也沿用建单原因。列表 / 详情 / 订单时间线都用它显示。
"""

from __future__ import annotations

ORDERS = "/api/v1/orders"
EXCEPTIONS = "/api/v1/exceptions"


def _order(client, headers, bootstrap, *, admin_headers=None, with_vehicle: bool) -> dict:
    created = client.post(
        ORDERS,
        headers=admin_headers or headers,
        json={
            "customer_id": bootstrap["customers"]["vip"].id,
            "origin_city": "天津",
            "dest_city": "上海",
            "distance_km": 800,
        },
    ).json()
    if with_vehicle:
        patched = client.patch(
            f"{ORDERS}/{created['id']}",
            headers=headers,
            json={"carrier_id": bootstrap["carrier"].id, "vehicle_id": bootstrap["vehicle"].id},
        )
        assert patched.status_code == 200, patched.text
    return created


def test_manual_create_without_type_infers_origin_and_current_type(
    client, bootstrap, operator_headers, admin_headers
):
    """界面不再传 type：后端按订单现场推建单原因，响应同时给出「当前问题」。"""
    order = _order(client, operator_headers, bootstrap, admin_headers=admin_headers, with_vehicle=True)
    response = client.post(
        EXCEPTIONS,
        headers=operator_headers,
        json={
            "order_id": order["id"],
            "occurred_at": bootstrap["base_time"].isoformat(),
            "note": "右后轮爆胎，已联系修理厂",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["type"] == "VEHICLE_BREAKDOWN", "有车的订单：建单原因按车辆故障记"
    assert body["current_type"] == "VEHICLE_BREAKDOWN", "车辆被置维修中 → 当前问题就是车辆故障"

    # 列表与详情都要带上 current_type（前端据此显示，不再用固定的 type）
    listed = client.get(EXCEPTIONS, headers=operator_headers, params={"page_size": 50}).json()["items"]
    row = next(item for item in listed if item["id"] == body["id"])
    assert row["current_type"] == "VEHICLE_BREAKDOWN"
    detail = client.get(f"{EXCEPTIONS}/{body['id']}", headers=operator_headers).json()
    assert detail["current_type"] == "VEHICLE_BREAKDOWN"
    # 订单侧的 open_exception 摘要也要带（订单页/时间线显示用）
    order_detail = client.get(f"{ORDERS}/{order['id']}", headers=operator_headers).json()
    if order_detail.get("open_exception"):
        assert order_detail["open_exception"]["current_type"] == "VEHICLE_BREAKDOWN"


def test_current_type_follows_reality_not_origin(client, bootstrap, operator_headers, admin_headers):
    """车辆问题解除后：建单原因不变；「当前问题」不再错标成延误 —— 没有车辆故障因子就沿用建单原因。"""
    order = _order(client, operator_headers, bootstrap, admin_headers=admin_headers, with_vehicle=True)
    created = client.post(
        EXCEPTIONS,
        headers=operator_headers,
        json={
            "order_id": order["id"],
            "occurred_at": bootstrap["base_time"].isoformat(),
            "note": "爆胎处理中",
        },
    ).json()
    assert created["current_type"] == "VEHICLE_BREAKDOWN"

    cleared = client.post(
        f"{EXCEPTIONS}/{created['id']}/clear-vehicle-issue",
        headers=operator_headers,
        json={"expected_version": created["version"], "note": "轮胎已更换"},
    )
    assert cleared.status_code == 200, cleared.text
    body = cleared.json()
    assert body["type"] == "VEHICLE_BREAKDOWN", "建单原因保留为历史"
    # 2026-10-06：没有车辆故障因子时沿用建单原因，不再无条件兜底成"延误风险"
    # （车辆故障单不做 SLA 判定，永远不会带延误因子；叫它延误是错标）
    assert body["current_type"] == "VEHICLE_BREAKDOWN", "车已修好但单子还挂着 → 仍是车辆故障（沿用建单原因）"
    assert body["status"] == "DETECTED", "异常单本身继续存在"

    # 结束之后就"没有当前了"：current_type 回到建单原因（历史判定口径）
    closed = client.post(
        f"{EXCEPTIONS}/{created['id']}/close",
        headers=operator_headers,
        json={"reason_code": "MANUAL", "note": "验证用归档", "expected_version": body["version"]},
    )
    assert closed.status_code == 200, closed.text
    assert closed.json()["current_type"] == "VEHICLE_BREAKDOWN", "已结束的单不再实时推导"
    # 已关闭 → 当前风险 0（列表「等级」列显示的就是它；历史等级仍在 level / risk_score 存档里）
    assert closed.json()["current_risk_score"] == 0
    assert closed.json()["current_level"] == "LOW"
    # 存档等级：解除车辆故障后只剩 VIP 1 分 → MEDIUM（详情页『历史判定』卡片用的就是它）
    assert closed.json()["level"] == "MEDIUM", "历史等级保留（详情页『历史判定』卡片要用）"
    assert closed.json()["risk_score"] == 1

    listed = client.get(EXCEPTIONS, headers=operator_headers, params={"page_size": 50}).json()["items"]
    row = next(item for item in listed if item["id"] == created["id"])
    assert row["current_risk_score"] == 0 and row["current_level"] == "LOW"


def test_manual_create_without_vehicle_infers_delay_origin(
    client, bootstrap, operator_headers, admin_headers
):
    """没派车的订单：建单原因按延误风险记。"""
    order = _order(client, operator_headers, bootstrap, admin_headers=admin_headers, with_vehicle=False)
    response = client.post(
        EXCEPTIONS,
        headers=operator_headers,
        json={
            "order_id": order["id"],
            "occurred_at": bootstrap["base_time"].isoformat(),
            "note": "客户催单，口岸压港",
        },
    )
    if response.status_code != 201:
        # 后端若禁止在未派车订单上建单，这条口径就不适用（保持显式失败，避免静默跳过）
        assert response.status_code == 409, response.text
        return
    body = response.json()
    assert body["type"] == "DELAY_RISK", "没有车辆 → 建单原因按延误风险记"
    assert body["current_type"] == "DELAY_RISK"
