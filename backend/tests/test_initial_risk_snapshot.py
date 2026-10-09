"""建单时风险快照（口径 2026-10-08）：**建单写一次，之后任何重算都不改**。

用户要求原话："具体的异常订单里，应该仍然保持异常订单出现时的值，把他写死在那里不再改变"。
覆盖：建单即冻结 / 重算只改"当前"不改快照 / 详情接口暴露三个快照字段。
"""

from __future__ import annotations

from datetime import timedelta

from app.services.common import to_naive_utc

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


def _set_planned(client, db_session, bootstrap, order_id: int, *, promised, offset_minutes: int, headers) -> dict:
    planned = promised + timedelta(minutes=offset_minutes)
    response = client.patch(
        f"{ORDERS}/{order_id}",
        headers=headers,
        json={"planned_delivery_at": planned.strftime("%Y-%m-%dT%H:%M:%S")},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_initial_risk_snapshot_is_frozen_at_creation(client, db_session, bootstrap, operator_headers, admin_headers):
    """延误 400 分钟建单（3+1=4 分 / CRITICAL）→ 改成 10 分钟（1 分）后：当前变了，快照不动。"""
    order = _order_with_vehicle(client, operator_headers, bootstrap, admin_headers=admin_headers)
    from app.repositories import Repos
    from app.services.orders import OrderService

    repos = Repos(db_session, workspace_id=bootstrap["workspace_id"])
    OrderService(repos).append_tracking(
        order["id"],
        event_type="DEPART",
        city="天津",
        source="OPERATOR",
        occurred_at=bootstrap["base_time"].isoformat(),
        trigger_detection=False,
    )
    db_session.commit()
    promised = to_naive_utc(repos.orders.get(order["id"]).promised_delivery_at)
    assert promised is not None

    # 建单：预计到达 = 承诺 + 400 分钟（VIP 规则允许 0 分钟 → 违约）
    _set_planned(
        client, db_session, bootstrap, order["id"], promised=promised, offset_minutes=400, headers=admin_headers
    )
    cases = client.get(f"{ORDERS}/{order['id']}/exceptions", headers=operator_headers).json()
    assert len(cases) == 1, cases
    case_id = cases[0]["id"]

    detail = client.get(f"{EXCEPTIONS}/{case_id}", headers=operator_headers).json()
    # 建单快照 = 建单时的判定（延误档位 3 + VIP 1 = 4 → CRITICAL）
    assert detail["initial_risk_score"] == 4, detail.get("initial_risk_score")
    assert detail["initial_level"] == "CRITICAL"
    assert {item["code"] for item in detail["initial_risk_factors"]} == {"DELAY_BASE", "CUSTOMER_VIP"}
    # 建单那一刻，"当前"与"快照"当然一致
    assert detail["risk_score"] == detail["initial_risk_score"]
    assert detail["level"] == detail["initial_level"]

    # 改预计到达时间（400 → 准时，延误 0 → 档位 0）：重算当前风险，但**快照必须原样不动**
    _set_planned(
        client, db_session, bootstrap, order["id"], promised=promised, offset_minutes=0, headers=admin_headers
    )
    after = client.get(f"{EXCEPTIONS}/{case_id}", headers=operator_headers).json()
    assert after["risk_score"] == 1, after["risk_score"]  # 档位 0 + VIP 1
    assert after["level"] == "MEDIUM"
    assert after["initial_risk_score"] == 4, "建单快照被重算覆盖了！"
    assert after["initial_level"] == "CRITICAL"
    assert {item["code"] for item in after["initial_risk_factors"]} == {"DELAY_BASE", "CUSTOMER_VIP"}
    # 因子明细也要是建单时的（延误 400 分钟），不是重算后的
    delay_factor = next(item for item in after["initial_risk_factors"] if item["code"] == "DELAY_BASE")
    assert delay_factor["weight"] == 3
    assert "400" in str(delay_factor["detail"]), delay_factor


def test_initial_risk_snapshot_survives_end_and_seed(client, db_session, bootstrap, operator_headers, admin_headers):
    """造数（seed）路径同样要有快照：不能出现 initial_risk_score 为空的单。"""
    from app.seed import reset_demo_data

    reset_demo_data(db_session, workspace_id=bootstrap["workspace_id"], with_knowledge=False)
    db_session.commit()

    listing = client.get(f"{EXCEPTIONS}?page_size=50", headers=operator_headers).json()["items"]
    assert listing, "造数应当产生异常单"
    for item in listing:
        detail = client.get(f"{EXCEPTIONS}/{item['id']}", headers=operator_headers).json()
        assert detail["initial_risk_score"] is not None, f"{item['case_no']} 没有建单快照"
        assert detail["initial_level"] in {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
        # 快照等级必须与快照分数自洽（0→LOW / 1-2→MEDIUM / 3→HIGH / 4→CRITICAL）
        score = int(detail["initial_risk_score"])
        expected = "LOW" if score <= 0 else "MEDIUM" if score <= 2 else "HIGH" if score == 3 else "CRITICAL"
        assert detail["initial_level"] == expected, (item["case_no"], score, detail["initial_level"])


def test_manual_creation_also_freezes_and_survives_resolution(
    client, db_session, bootstrap, operator_headers, admin_headers
):
    """人工建单路径同样要有快照；**解决后"当前"归 0，快照必须仍是建单时的值**。"""
    order = _order_with_vehicle(client, operator_headers, bootstrap, admin_headers=admin_headers)
    created = client.post(
        EXCEPTIONS,
        headers=operator_headers,
        json={
            "order_id": order["id"],
            "type": "VEHICLE_BREAKDOWN",
            "occurred_at": bootstrap["base_time"].isoformat(),
            "note": "快照测试：车辆故障",
        },
    ).json()
    case_id = created["id"]

    detail = client.get(f"{EXCEPTIONS}/{case_id}", headers=operator_headers).json()
    # 人工建单会把车辆置"维修中" → 车辆故障因子按现状计分：1 + VIP 1 = 2 → MEDIUM
    assert detail["initial_risk_score"] == detail["risk_score"], detail
    assert detail["initial_risk_score"] == 2, detail["initial_risk_score"]
    assert detail["initial_level"] == "MEDIUM"
    assert {item["code"] for item in detail["initial_risk_factors"]} == {
        "VEHICLE_BREAKDOWN",
        "CUSTOMER_VIP",
    }

    # 确认 → 解决：当前风险归 0（列表显示"无风险"），但快照必须原样不动
    confirmed = client.post(
        f"{EXCEPTIONS}/{case_id}/confirm",
        headers=operator_headers,
        json={"expected_version": detail["version"]},
    ).json()
    resolved = client.post(
        f"{EXCEPTIONS}/{case_id}/resolve",
        headers=operator_headers,
        json={"note": "快照测试：处理完", "expected_version": confirmed["version"]},
    )
    assert resolved.status_code == 200, resolved.text

    after = client.get(f"{EXCEPTIONS}/{case_id}", headers=operator_headers).json()
    assert after["status"] == "RESOLVED"
    assert after["current_risk_score"] == 0, after["current_risk_score"]
    assert after["initial_risk_score"] == 2, "解决后建单快照被改动了！"
    assert after["initial_level"] == "MEDIUM"
    assert {item["code"] for item in after["initial_risk_factors"]} == {"VEHICLE_BREAKDOWN", "CUSTOMER_VIP"}
