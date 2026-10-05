"""送达口径（2026-10-05 新模型）：延误只在**订单送达时**按实际时间判定，并可按实际送达时间纠错。

用户口径：
- 「只有当我在运输订单中选择了送达……如果实际送达时间减去承诺送达时间按照 sla 规则进行比较，
   出现风险等级的时候，再自动生成异常订单」；
- 「可以留着修改功能，用于……没有正确输入送达时间后的修改」。

覆盖：
a) 送达超允许延迟 → 自动建延误单（DETECTED / DELIVERED_BREACH / 延误=实际−承诺 / 违约 / 分数=档位+客户等级）；
b) 送达未超 → **不建单**；
c) 送达那刻还开着的车辆单先被收口（处理中→已解决），**再**按实际时间决定要不要建延误单；
d) 修正实际送达时间：仍违约 → 重算延误与分数；不再违约 → 该延误单自动收口（处理中→已解决；待确认→已关闭/误报）；
e) 未送达订单调用 → 409；OPERATOR → 403；早于派车时间 → 422；
f) 写 `order.delivered_at_corrected` 审计（before/after 带 delivered_at）。
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from app.models import AuditLog
from app.models.exception import ExceptionCase
from app.repositories import Repos
from app.services.common import to_naive_utc
from app.services.exceptions import ExceptionService
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
    """发车（IN_TRANSIT）后按"承诺 + offset_minutes"送达（offset>允许延迟即违约）。"""
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


def _cases(client, headers, order_id: int) -> list[dict]:
    return client.get(f"{ORDERS}/{order_id}/exceptions", headers=headers).json()


def test_delivered_late_creates_delay_case(client, db_session, bootstrap, operator_headers, admin_headers):
    """a) 送达超允许延迟（VIP 规则允许 0 分钟；晚 400 分钟）→ 自动建延误单。"""
    order = _order_with_vehicle(client, operator_headers, bootstrap, admin_headers=admin_headers)
    promised, _ = _deliver(db_session, bootstrap, order["id"], offset_minutes=400)

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

    events = client.get(f"{EXCEPTIONS}/{case['id']}/events", headers=operator_headers).json()["items"]
    assert any(event["event_type"] == "DETECTED" for event in events), events
    audits = list(
        db_session.scalars(
            select(AuditLog).where(AuditLog.action == "exception.detected", AuditLog.resource_id == case["id"])
        ).all()
    )
    assert audits, "自动建单要留审计"
    assert promised is not None


def test_delivered_on_time_creates_no_case(client, db_session, bootstrap, operator_headers, admin_headers):
    """b) 送达未超允许延迟 → 不建任何异常单。"""
    order = _order_with_vehicle(
        client, operator_headers, bootstrap, admin_headers=admin_headers, customer="normal"
    )
    _deliver(db_session, bootstrap, order["id"], offset_minutes=25)  # NORMAL 允许 30 分钟
    assert _cases(client, operator_headers, order["id"]) == []


def test_delivery_first_closes_open_vehicle_case(client, db_session, bootstrap, operator_headers, admin_headers):
    """c) 送达先把还开着的车辆单收口（处理中→已解决），再按实际时间决定是否建延误单。"""
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

    _deliver(db_session, bootstrap, order["id"], offset_minutes=200)  # NORMAL 允许 30 → 违约

    cases = _cases(client, operator_headers, order["id"])
    by_type = {case["type"]: case for case in cases}
    assert by_type["VEHICLE_BREAKDOWN"]["status"] == "RESOLVED", by_type
    assert by_type["DELAY_RISK"]["status"] == "DETECTED", by_type
    assert by_type["DELAY_RISK"]["detection_rule"] == "DELIVERED_BREACH"
    assert by_type["DELAY_RISK"]["sla_delay_minutes"] == 200


def test_correct_delivered_at_to_on_time_closes_delay_case(
    client, db_session, bootstrap, operator_headers, admin_headers
):
    """d-1) 送达时间录错：修正为"按时"→ 该延误单自动收口（待确认 → 已关闭/误报）。"""
    order = _order_with_vehicle(client, operator_headers, bootstrap, admin_headers=admin_headers)
    promised, _ = _deliver(db_session, bootstrap, order["id"], offset_minutes=400)
    case = _cases(client, operator_headers, order["id"])[0]
    assert case["status"] == "DETECTED"

    fixed = client.patch(
        f"{ORDERS}/{order['id']}/delivered-at",
        headers=admin_headers,
        json={"delivered_at": promised.strftime("%Y-%m-%dT%H:%M:%S"), "note": "实为按时送达，之前录错"},
    )
    assert fixed.status_code == 200, fixed.text

    after = client.get(f"{EXCEPTIONS}/{case['id']}", headers=operator_headers).json()
    assert after["status"] == "CLOSED", after  # 待确认只能归档为已关闭（状态机合法出口）
    assert after["close_reason"] == "INVALID"
    assert after["current_risk_score"] == 0 and after["current_level"] == "LOW"
    assert after["sla_breached"] is False


def test_correct_delivered_at_still_breaching_recomputes(client, db_session, bootstrap, operator_headers, admin_headers):
    """d-2) 修正后仍违约（400 → 200 分钟）→ 延误与分数按新时间重算。"""
    order = _order_with_vehicle(client, operator_headers, bootstrap, admin_headers=admin_headers)
    promised, _ = _deliver(db_session, bootstrap, order["id"], offset_minutes=400)
    case = _cases(client, operator_headers, order["id"])[0]
    assert int(case["risk_score"]) == 4

    fixed = client.patch(
        f"{ORDERS}/{order['id']}/delivered-at",
        headers=admin_headers,
        json={
            "delivered_at": (promised + timedelta(minutes=200)).strftime("%Y-%m-%dT%H:%M:%S"),
            "note": "实际为晚 200 分钟",
        },
    )
    assert fixed.status_code == 200, fixed.text

    after = client.get(f"{EXCEPTIONS}/{case['id']}", headers=operator_headers).json()
    assert after["sla_delay_minutes"] == 200
    assert after["sla_breached"] is True
    assert int(after["risk_score"]) == 3 and after["level"] == "HIGH"  # 档位 2 + VIP 1
    assert after["status"] == "DETECTED"


def test_correct_delivered_at_rejects_undelivered_and_operator(
    client, db_session, bootstrap, operator_headers, admin_headers
):
    """e) 未送达 → 409；OPERATOR 无 order.manage → 403；早于派车时间 → 422。"""
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
    """f) 修正实际送达时间写 `order.delivered_at_corrected` 审计（before/after 带 delivered_at）。"""
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
    # 审计里的时间按 ISO 串（…T…）序列化存储
    assert str(row.before_json.get("delivered_at")).replace("T", " ") == str(delivered_at)
    expected_after = promised + timedelta(minutes=100)
    assert str(row.after_json.get("delivered_at")).replace("T", " ") == str(expected_after)
    assert row.after_json.get("note") == "纠错"
    assert db_session.get(ExceptionCase, _cases(client, operator_headers, order["id"])[0]["id"]) is not None
    assert ExceptionService  # 引用保持（本文件用到服务层语义）
