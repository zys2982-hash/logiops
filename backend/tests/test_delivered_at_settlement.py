"""延误结算口径测试（**2026-10-08 新口径：按「预计到达时间」判定**）。

口径对照（用户亲自定的两版）：

|  | 旧（2026-10-05 ~ 10-08） | 现（2026-10-08 起） |
|---|---|---|
| 判定时点 | `order.delivered_at`（实际送达） | `order.planned_delivery_at`（预计到达） |
| 触发点 | 订单送达 / 修正实际送达 | **保存或修改预计到达时间** |
| 没填预计到达 | —— | **不判定** |

覆盖：按预计到达建单 / 未超不建单 / 送达不再建单 / 与车辆单并存 / 改预计到达重算 /
修正实际送达不影响延误 / 修正实际送达的校验与审计 / 摘要用北京时间。
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from app.models import AuditLog
from app.models.exception import ExceptionCase
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


def _repos(db_session, bootstrap) -> Repos:
    return Repos(db_session, workspace_id=bootstrap["workspace_id"])


def _deliver(db_session, bootstrap, order_id: int, *, offset_minutes: int):
    """发车（IN_TRANSIT）后按"承诺 + offset_minutes"送达 —— 现口径下**不再**触发延误判定。"""
    repos = _repos(db_session, bootstrap)
    OrderService(repos).append_tracking(
        order_id,
        event_type="DEPART",
        city="天津",
        source="OPERATOR",
        occurred_at=bootstrap["base_time"].isoformat(),
        trigger_detection=False,
    )
    promised = to_naive_utc(repos.orders.get(order_id).promised_delivery_at)
    assert promised is not None
    delivered_at = promised + timedelta(minutes=offset_minutes)
    OrderService(repos).mark_delivered(order_id, occurred_at=delivered_at, actor_id=None)
    db_session.commit()
    return promised, delivered_at


def _set_planned(client, db_session, bootstrap, order_id: int, *, offset_minutes: int, headers):
    """把「预计到达时间」设成 承诺 + offset —— **新口径的判定触发点**（走 PATCH /orders/{id}）。"""
    repos = _repos(db_session, bootstrap)
    promised = to_naive_utc(repos.orders.get(order_id).promised_delivery_at)
    assert promised is not None
    planned = promised + timedelta(minutes=offset_minutes)
    response = client.patch(
        f"{ORDERS}/{order_id}",
        headers=headers,
        json={"planned_delivery_at": planned.strftime("%Y-%m-%dT%H:%M:%S")},
    )
    assert response.status_code == 200, response.text
    return promised, planned


def _cases(client, headers, order_id: int) -> list[dict]:
    return client.get(f"{ORDERS}/{order_id}/exceptions", headers=headers).json()


def test_planned_late_creates_delay_case(client, db_session, bootstrap, operator_headers, admin_headers):
    """a) 预计到达超允许延迟（VIP 规则允许 0 分钟；晚 400 分钟）→ 自动建延误单。

    新增口径特征：**不需要订单已送达** —— 保存预计到达时间那刻就判定并建单。
    """
    order = _order_with_vehicle(client, operator_headers, bootstrap, admin_headers=admin_headers)
    _set_planned(client, db_session, bootstrap, order["id"], offset_minutes=400, headers=admin_headers)

    cases = _cases(client, operator_headers, order["id"])
    assert len(cases) == 1, cases
    case = cases[0]
    assert case["type"] == "DELAY_RISK"
    assert case["status"] == "DETECTED"  # 机器建单 → 待人工确认
    assert case["detected_by"] == "SYSTEM"
    assert case["detection_rule"] == "DELIVERED_BREACH"
    assert case["sla_delay_minutes"] == 400
    assert case["sla_breached"] is True
    assert {f["code"] for f in case["risk_factors"]} == {"DELAY_BASE", "CUSTOMER_VIP"}
    # 延误 400min → 档位 3 + VIP 1 = 4 → CRITICAL
    assert int(case["risk_score"]) == 4 and case["level"] == "CRITICAL"
    assert case["promised_delivery_at"] is not None

    # 订单还没送达也照样判定（新口径）
    fresh = client.get(f"{ORDERS}/{order['id']}", headers=admin_headers).json()
    assert fresh["status"] != "DELIVERED"
    assert fresh["delivered_at"] is None

    events = client.get(f"{EXCEPTIONS}/{case['id']}/events", headers=operator_headers).json()["items"]
    assert any(event["event_type"] == "DETECTED" for event in events), events
    audits = list(
        db_session.scalars(
            select(AuditLog).where(AuditLog.action == "exception.detected", AuditLog.resource_id == case["id"])
        ).all()
    )
    assert audits, "自动建单要留审计"


def test_planned_on_time_creates_no_case(client, db_session, bootstrap, operator_headers, admin_headers):
    """b) 预计到达未超允许延迟 → 不建任何异常单。"""
    order = _order_with_vehicle(
        client, operator_headers, bootstrap, admin_headers=admin_headers, customer="normal"
    )
    _set_planned(client, db_session, bootstrap, order["id"], offset_minutes=25, headers=admin_headers)
    assert _cases(client, operator_headers, order["id"]) == []  # NORMAL 允许 30 分钟


def test_delivery_no_longer_creates_delay_case(client, db_session, bootstrap, operator_headers, admin_headers):
    """c) **口径变更的护栏**：送达再晚也不建延误单（送达不再参与延误判定）。"""
    order = _order_with_vehicle(client, operator_headers, bootstrap, admin_headers=admin_headers)
    _deliver(db_session, bootstrap, order["id"], offset_minutes=400)
    assert _cases(client, operator_headers, order["id"]) == [], "送达不再触发延误判定"


def test_planned_delay_case_and_vehicle_case_coexist(
    client, db_session, bootstrap, operator_headers, admin_headers
):
    """d) 车辆单 + 延误单并存（各管一个问题），送达也不替用户收口车辆单。"""
    order = _order_with_vehicle(
        client, operator_headers, bootstrap, admin_headers=admin_headers, customer="normal"
    )
    created = client.post(
        EXCEPTIONS,
        headers=operator_headers,
        json={
            "order_id": order["id"],
            "type": "VEHICLE_BREAKDOWN",
            "occurred_at": bootstrap["base_time"].isoformat(),
            "note": "车辆在济南爆胎",
        },
    ).json()
    confirmed = client.post(
        f"{EXCEPTIONS}/{created['id']}/confirm",
        headers=operator_headers,
        json={"expected_version": created["version"]},
    ).json()
    assert confirmed["status"] == "PROCESSING"

    # 新口径：延误由「预计到达」触发（NORMAL 允许 30 → 设成晚 200 分钟即违约）
    _set_planned(client, db_session, bootstrap, order["id"], offset_minutes=200, headers=admin_headers)
    # 送达同样发生（它仍要释放车辆、对齐车辆故障因子，只是不参与延误判定）
    _deliver(db_session, bootstrap, order["id"], offset_minutes=200)

    cases = _cases(client, operator_headers, order["id"])
    by_type = {case["type"]: case for case in cases}
    assert by_type["VEHICLE_BREAKDOWN"]["status"] == "PROCESSING", by_type
    assert by_type["VEHICLE_BREAKDOWN"]["resolved_at"] is None
    assert by_type["VEHICLE_BREAKDOWN"]["risk_factors"] == [], by_type
    assert by_type["VEHICLE_BREAKDOWN"]["current_risk_score"] == 0, by_type
    assert by_type["DELAY_RISK"]["status"] == "DETECTED", by_type
    assert by_type["DELAY_RISK"]["detection_rule"] == "DELIVERED_BREACH"
    assert by_type["DELAY_RISK"]["sla_delay_minutes"] == 200
    vehicle = client.get(f"/api/v1/vehicles/{bootstrap['vehicle'].id}", headers=operator_headers).json()
    assert vehicle["status"] == "IDLE", vehicle


def test_change_planned_recomputes_delay_case(client, db_session, bootstrap, operator_headers, admin_headers):
    """e) 改「预计到达时间」→ 延误与分数按新时间重算（400 → 200 分钟），不自动收口。"""
    order = _order_with_vehicle(client, operator_headers, bootstrap, admin_headers=admin_headers)
    _set_planned(client, db_session, bootstrap, order["id"], offset_minutes=400, headers=admin_headers)
    case = _cases(client, operator_headers, order["id"])[0]
    assert int(case["risk_score"]) == 4

    _set_planned(client, db_session, bootstrap, order["id"], offset_minutes=200, headers=admin_headers)

    after = client.get(f"{EXCEPTIONS}/{case['id']}", headers=operator_headers).json()
    assert after["sla_delay_minutes"] == 200
    assert after["sla_breached"] is True
    assert int(after["risk_score"]) == 3 and after["level"] == "HIGH"  # 档位 2 + VIP 1
    assert after["status"] == "DETECTED"  # 仍待人工处置
    assert after["resolved_at"] is None and after["closed_at"] is None

    events = client.get(f"{EXCEPTIONS}/{case['id']}/events", headers=operator_headers).json()["items"]
    assert any(event["event_type"] == "ETA_UPDATED" for event in events), events


def test_correct_delivered_at_does_not_affect_delay(
    client, db_session, bootstrap, operator_headers, admin_headers
):
    """f) **口径护栏**：修正实际送达时间不再改延误（延误只看预计到达）。"""
    order = _order_with_vehicle(client, operator_headers, bootstrap, admin_headers=admin_headers)
    _set_planned(client, db_session, bootstrap, order["id"], offset_minutes=400, headers=admin_headers)
    case = _cases(client, operator_headers, order["id"])[0]
    assert case["sla_delay_minutes"] == 400

    _, delivered = _deliver(db_session, bootstrap, order["id"], offset_minutes=400)
    fixed = client.patch(
        f"{ORDERS}/{order['id']}/delivered-at",
        headers=admin_headers,
        json={"delivered_at": delivered.strftime("%Y-%m-%dT%H:%M:%S"), "note": "把实际送达往前挪"},
    )
    assert fixed.status_code == 200, fixed.text

    after = client.get(f"{EXCEPTIONS}/{case['id']}", headers=operator_headers).json()
    assert after["sla_delay_minutes"] == 400, "修正实际送达不应改延误（判定用预计到达）"
    assert after["status"] == "DETECTED"


def test_correct_delivered_at_rejects_undelivered_and_operator(
    client, db_session, bootstrap, operator_headers, admin_headers
):
    """g) 修正实际送达：未送达 → 409；OPERATOR 无 order.manage → 403；早于派车时间 → 422。"""
    order = _order_with_vehicle(client, operator_headers, bootstrap, admin_headers=admin_headers)
    not_delivered = client.patch(
        f"{ORDERS}/{order['id']}/delivered-at",
        headers=admin_headers,
        json={"delivered_at": "2026-10-05T00:00:00", "note": "还没送达"},
    )
    assert not_delivered.status_code == 409, not_delivered.text

    _deliver(db_session, bootstrap, order["id"], offset_minutes=400)
    denied = client.patch(
        f"{ORDERS}/{order['id']}/delivered-at",
        headers=operator_headers,
        json={"delivered_at": "2026-10-05T00:00:00", "note": "越权"},
    )
    assert denied.status_code == 403, denied.text

    too_early = client.patch(
        f"{ORDERS}/{order['id']}/delivered-at",
        headers=admin_headers,
        json={"delivered_at": "2000-01-01T00:00:00", "note": "早于派车"},
    )
    assert too_early.status_code == 422, too_early.text


def test_correct_delivered_at_writes_audit(client, db_session, bootstrap, operator_headers, admin_headers):
    """h) 修正实际送达时间写 `order.delivered_at_corrected` 审计（before/after 带 delivered_at）。"""
    order = _order_with_vehicle(client, operator_headers, bootstrap, admin_headers=admin_headers)
    promised, delivered_at = _deliver(db_session, bootstrap, order["id"], offset_minutes=400)

    fixed = client.patch(
        f"{ORDERS}/{order['id']}/delivered-at",
        headers=admin_headers,
        json={"delivered_at": (promised + timedelta(minutes=100)).strftime("%Y-%m-%dT%H:%M:%S"), "note": "纠错"},
    )
    assert fixed.status_code == 200, fixed.text

    rows = list(
        db_session.scalars(
            select(AuditLog).where(
                AuditLog.action == "order.delivered_at_corrected",
                AuditLog.resource_id == order["id"],
            )
        ).all()
    )
    assert rows, "必须留审计"
    row = rows[-1]
    assert str(row.before_json.get("delivered_at")).replace("T", " ") == str(delivered_at)
    expected_after = promised + timedelta(minutes=100)
    assert str(row.after_json.get("delivered_at")).replace("T", " ") == str(expected_after)
    assert row.after_json.get("note") == "纠错"
    # 送达不再自动建单 → 这张订单此时应当没有任何异常单
    assert _cases(client, operator_headers, order["id"]) == []


def test_delay_case_impact_summary_uses_beijing_time(
    client, db_session, bootstrap, operator_headers, admin_headers
):
    """i) 摘要整串落库、前端原样显示 → 里面的时间必须是**北京时间**（口径 2026-10-08：都用北京时间）。

    历史 bug：摘要曾在服务端用 Python 裸 datetime 拼串 → 存的是 UTC，
    界面上出现"页头 16:09、摘要 08:09"的同屏不一致。本用例不复用被测代码算期望值，
    直接用「朴素 UTC + 8 小时」独立校验。
    """
    order = _order_with_vehicle(client, operator_headers, bootstrap, admin_headers=admin_headers)
    promised, planned = _set_planned(
        client, db_session, bootstrap, order["id"], offset_minutes=400, headers=admin_headers
    )

    summary = _cases(client, operator_headers, order["id"])[0]["impact_summary"]
    expected_planned = (planned + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")
    expected_promised = (promised + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")
    assert expected_planned in summary, summary
    assert expected_promised in summary, summary
    # 反向断言：不能再出现 UTC 写法
    assert planned.strftime("%Y-%m-%d %H:%M:%S") not in summary, summary
    assert promised.strftime("%Y-%m-%d %H:%M:%S") not in summary, summary


def test_delay_case_persists_expected_eta_and_case_link(client, db_session, bootstrap, operator_headers, admin_headers):
    """j) 建单后库里留痕正确：异常单存在、`expected_eta_at` = 预计到达（判定时点）。"""
    order = _order_with_vehicle(client, operator_headers, bootstrap, admin_headers=admin_headers)
    _, planned = _set_planned(
        client, db_session, bootstrap, order["id"], offset_minutes=400, headers=admin_headers
    )
    case_id = _cases(client, operator_headers, order["id"])[0]["id"]
    db_session.expire_all()
    case = db_session.get(ExceptionCase, case_id)
    assert case is not None
    assert to_naive_utc(case.expected_eta_at) == planned
    assert case.sla_delay_minutes == 400
