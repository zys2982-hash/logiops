"""风险可解释性：因子的事实来源 + 这张单由哪些在途信号构成。

用户口径（原话）："订单里的在途异常应该归于异常中心风险等级的一部分组份……
我感觉现在一个在途异常就对应了一个异常中心的异常订单"。
确认下来是两层：异常单粒度 = 一个订单一张（检测只合并 / 手工建单会 409），
而在途信号以 risk_factors 参与定级。所以详情页要把「信号 → 因子」这条链显式给出。

覆盖：
- 每个因子都能说出"为什么存在"（人工录入延误 / 客户等级 / 车辆现状 / SLA 承诺与预计）；
- 信号流包含 检测建单 / 人工录入延误 / 轨迹信号；
- 车辆故障因子的来源指向**车辆当前状态**（维修中才计入）；
- 列表接口不带这层解释（只有详情才返回，避免列表变重）。
"""

from __future__ import annotations

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


def _create_delay_case(client, headers, bootstrap, order_id: int, delay_minutes: int) -> dict:
    response = client.post(
        EXCEPTIONS,
        headers=headers,
        json={
            "order_id": order_id,
            "type": "DELAY_RISK",
            "occurred_at": bootstrap["base_time"].isoformat(),
            "note": "京沪高速拥堵，预计延误 300 分钟",
            "delay_minutes": delay_minutes,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _explanation(client, headers, case_id: int) -> dict:
    detail = client.get(f"{EXCEPTIONS}/{case_id}", headers=headers).json()
    return detail["risk_explanation"]


def test_factor_sources_and_signal_flow(client, db_session, bootstrap, operator_headers, admin_headers):
    order = _order_with_vehicle(client, operator_headers, bootstrap, admin_headers=admin_headers)
    case = _create_delay_case(client, operator_headers, bootstrap, order["id"], delay_minutes=300)

    # 在途事实：写入一条轨迹信号（会同步跑检测，若命中就合并进这张单）
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
    assert by_code, "风险因子不能为空"

    # 延误因子：只补"表格里没有的信息" —— 人工录入事实（不再重复"规则快照"那一行）
    delay_texts = [source["text"] for source in by_code["DELAY_BASE"]["sources"]]
    assert any("人工录入延误 300 分钟" in text for text in delay_texts), delay_texts
    assert not any("规则快照" in text for text in delay_texts), f"「说明」列已写延误分钟，不该再重复：{delay_texts}"
    # VIP 因子：指向客户等级
    assert by_code["CUSTOMER_VIP"]["sources"][0]["text"].startswith("客户")
    assert "VIP" in by_code["CUSTOMER_VIP"]["sources"][0]["text"]
    # 违约因子：一条说清 承诺 / 预计 / 规则阈值 / 超出多少
    breach_text = " ".join(source["text"] for source in by_code["SLA_BREACH"]["sources"])
    assert "承诺到达" in breach_text and "允许延误" in breach_text and "超出" in breach_text, breach_text

    kinds = {signal["kind"] for signal in explanation["signals"]}
    assert {"DETECTION", "MANUAL_DELAY", "TRACKING"} <= kinds, kinds
    assert explanation["summary"]["signals_total"] >= 3
    tracking_signals = [s for s in explanation["signals"] if s["kind"] == "TRACKING"]
    assert any("德州" in signal["text"] for signal in tracking_signals)


def test_vehicle_factor_points_to_current_vehicle_status(
    client, db_session, bootstrap, operator_headers, admin_headers
):
    order = _order_with_vehicle(client, operator_headers, bootstrap, admin_headers=admin_headers)
    created = client.post(
        EXCEPTIONS,
        headers=operator_headers,
        json={
            "order_id": order["id"],
            "type": "VEHICLE_BREAKDOWN",
            "occurred_at": bootstrap["base_time"].isoformat(),
            "note": "车辆在济南爆胎，已联系修理厂",
        },
    )
    assert created.status_code == 201, created.text
    case = created.json()

    explanation = _explanation(client, operator_headers, case["id"])
    by_code = {factor["code"]: factor for factor in explanation["factors"]}
    assert "VEHICLE_BREAKDOWN" in by_code, explanation["factors"]

    vehicle_source = next(
        source for source in by_code["VEHICLE_BREAKDOWN"]["sources"] if source["kind"] == "VEHICLE_STATUS"
    )
    assert bootstrap["vehicle"].plate_no in vehicle_source["text"]
    assert "维修中" in vehicle_source["text"] or "REPAIRING" in vehicle_source["text"]
    assert vehicle_source["ref"]["vehicle_id"] == bootstrap["vehicle"].id


def test_non_breached_case_keeps_rule_threshold_on_delay_factor(
    client, db_session, bootstrap, operator_headers, admin_headers
):
    """未违约时没有「SLA 已违约」因子，阈值就放在「延误时长」因子下（避免两处重复同一组数字）。"""
    order = _order_with_vehicle(
        client, operator_headers, bootstrap, admin_headers=admin_headers, customer="normal"
    )
    case = _create_delay_case(client, operator_headers, bootstrap, order["id"], delay_minutes=25)

    explanation = _explanation(client, operator_headers, case["id"])
    by_code = {factor["code"]: factor for factor in explanation["factors"]}
    assert "SLA_BREACH" not in by_code, "25 分钟 < 默认规则允许 30 分钟，不应有违约因子"

    texts = [source["text"] for source in by_code["DELAY_BASE"]["sources"]]
    assert any("允许延误" in text and "未超阈值" in text for text in texts), texts
    assert not any("规则快照" in text for text in texts), texts


def test_list_does_not_carry_explanation(client, db_session, bootstrap, operator_headers, admin_headers):
    order = _order_with_vehicle(client, operator_headers, bootstrap, admin_headers=admin_headers)
    _create_delay_case(client, operator_headers, bootstrap, order["id"], delay_minutes=45)

    items = client.get(EXCEPTIONS, headers=operator_headers).json()["items"]
    assert items, "列表应有刚建的异常"
    assert all(item.get("risk_explanation") is None for item in items), "列表不返回可解释明细（只在详情）"
