"""风险可解释性：因子的事实来源 + 这张单由哪些在途信号构成。

口径（2026-10-05）：
- **车辆故障单** = 车辆故障(1) + 客户等级；因子只有 `VEHICLE_BREAKDOWN` / `CUSTOMER_*`；
- **延误单** = 延误档位 + 客户等级（**只在订单送达后按"实际送达 − 承诺送达"产生**）；
  因子只有 `DELAY_BASE` / `CUSTOMER_*`，其"依据"给出 承诺到达 / 实际送达 / 规则允许 / 超出多少；
- 旧的 `SLA_BREACH` 因子不再产生（同一事实不重复计分），只在读历史老单时可能仍出现。

覆盖：
- 车辆单：车辆故障因子的来源指向**车辆当前状态**（维修中才计入）、VIP 因子指向客户等级；
- 延误单：因子与"承诺/实际送达"依据；
- 列表接口不带这层解释（只有详情才返回，避免列表变重）。
"""

from __future__ import annotations

from datetime import timedelta

from app.repositories import Repos
from app.services.common import to_naive_utc
from app.services.orders import OrderService

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


def _vehicle_case(client, headers, bootstrap, order_id: int, *, note: str = "车辆在济南爆胎，已联系修理厂") -> dict:
    response = client.post(
        EXCEPTIONS,
        headers=headers,
        json={
            "order_id": order_id,
            "type": "VEHICLE_BREAKDOWN",
            "occurred_at": bootstrap["base_time"].isoformat(),
            "note": note,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _explanation(client, headers, case_id: int) -> dict:
    detail = client.get(f"{EXCEPTIONS}/{case_id}", headers=headers).json()
    return detail["risk_explanation"]


def test_vehicle_case_factor_sources_and_signal_flow(
    client, db_session, bootstrap, operator_headers, admin_headers
):
    order = _order_with_vehicle(client, operator_headers, bootstrap, admin_headers=admin_headers)
    case = _vehicle_case(client, operator_headers, bootstrap, order["id"])

    # 在途事实：写一条轨迹信号
    tracked = client.post(
        f"{ORDERS}/{order['id']}/tracking-events",
        headers=operator_headers,
        json={
            "event_type": "NOTE",
            "city": "德州",
            "speed_kmh": 40,
            "source": "OPERATOR",
            "occurred_at": bootstrap["base_time"].isoformat(),
        },
    )
    assert tracked.status_code in (200, 201), tracked.text

    explanation = _explanation(client, operator_headers, case["id"])
    assert explanation["note"].startswith("同一订单同时只有一张未结束异常单")

    by_code = {factor["code"]: factor for factor in explanation["factors"]}
    assert set(by_code) == {"VEHICLE_BREAKDOWN", "CUSTOMER_VIP"}, by_code

    # 车辆故障因子：来源指向车辆现状
    vehicle_source = next(
        source for source in by_code["VEHICLE_BREAKDOWN"]["sources"] if source["kind"] == "VEHICLE_STATUS"
    )
    assert bootstrap["vehicle"].plate_no in vehicle_source["text"]
    # VIP 因子：指向客户等级
    assert by_code["CUSTOMER_VIP"]["sources"][0]["text"].startswith("客户")
    assert "VIP" in by_code["CUSTOMER_VIP"]["sources"][0]["text"]

    kinds = {signal["kind"] for signal in explanation["signals"]}
    assert {"DETECTION", "TRACKING"} <= kinds, kinds
    tracking_signals = [s for s in explanation["signals"] if s["kind"] == "TRACKING"]
    assert any("德州" in signal["text"] for signal in tracking_signals)


def test_vehicle_factor_points_to_current_vehicle_status(
    client, db_session, bootstrap, operator_headers, admin_headers
):
    order = _order_with_vehicle(client, operator_headers, bootstrap, admin_headers=admin_headers)
    case = _vehicle_case(client, operator_headers, bootstrap, order["id"])

    explanation = _explanation(client, operator_headers, case["id"])
    by_code = {factor["code"]: factor for factor in explanation["factors"]}
    assert "VEHICLE_BREAKDOWN" in by_code, explanation["factors"]

    vehicle_source = next(
        source for source in by_code["VEHICLE_BREAKDOWN"]["sources"] if source["kind"] == "VEHICLE_STATUS"
    )
    assert bootstrap["vehicle"].plate_no in vehicle_source["text"]
    assert "维修中" in vehicle_source["text"] or "REPAIRING" in vehicle_source["text"]
    assert vehicle_source["ref"]["vehicle_id"] == bootstrap["vehicle"].id


def test_delivered_delay_case_sources_show_promised_actual_and_allowance(
    client, db_session, bootstrap, operator_headers, admin_headers
):
    """延误单由**预计到达超时**产生（2026-10-08 新口径）：因子只有 延误时长 + 客户等级，
    依据给出 承诺到达 / **预计到达** / 允许延误 / 超出多少。"""
    order = _order_with_vehicle(client, operator_headers, bootstrap, admin_headers=admin_headers)
    repos = Repos(db_session, workspace_id=bootstrap["workspace_id"])
    OrderService(repos).append_tracking(
        order["id"],
        event_type="DEPART",
        city="天津",
        source="OPERATOR",
        occurred_at=bootstrap["base_time"].isoformat(),
        trigger_detection=False,
    )
    row = repos.orders.get(order["id"])
    assert row.promised_delivery_at is not None
    db_session.commit()

    # 新口径的触发点：保存「预计到达时间」= 承诺 + 400 分钟 → 自动建单
    planned = to_naive_utc(row.promised_delivery_at) + timedelta(minutes=400)
    patched = client.patch(
        f"{ORDERS}/{order['id']}",
        headers=admin_headers,
        json={"planned_delivery_at": planned.strftime("%Y-%m-%dT%H:%M:%S")},
    )
    assert patched.status_code == 200, patched.text

    cases = client.get(f"{ORDERS}/{order['id']}/exceptions", headers=operator_headers).json()
    assert len(cases) == 1, cases
    case = cases[0]
    assert case["type"] == "DELAY_RISK"
    assert case["detection_rule"] == "DELIVERED_BREACH"
    assert case["sla_breached"] is True
    # 延误 400min → 档位 3 + VIP 1 = 4 → CRITICAL
    assert int(case["risk_score"]) == 4 and case["level"] == "CRITICAL"

    explanation = _explanation(client, operator_headers, case["id"])
    by_code = {factor["code"]: factor for factor in explanation["factors"]}
    assert set(by_code) == {"DELAY_BASE", "CUSTOMER_VIP"}, by_code
    text = " ".join(source["text"] for source in by_code["DELAY_BASE"]["sources"])
    assert "承诺到达" in text and "预计到达" in text and "允许延误" in text and "超出" in text, text


def test_list_does_not_carry_explanation(client, db_session, bootstrap, operator_headers, admin_headers):
    order = _order_with_vehicle(client, operator_headers, bootstrap, admin_headers=admin_headers)
    _vehicle_case(client, operator_headers, bootstrap, order["id"])

    items = client.get(EXCEPTIONS, headers=operator_headers).json()["items"]
    assert items, "列表应有刚建的异常"
    assert all(item.get("risk_explanation") is None for item in items), "列表不返回可解释明细（只在详情）"
