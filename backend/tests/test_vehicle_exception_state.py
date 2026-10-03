"""车辆故障异常驱动的车辆状态（交接清单 ②，副标题「结束异常要能把车改回原状态」）。

口径（①→② 交接说明）：
- create_manual + type=VEHICLE_BREAKDOWN + 订单有车辆 → 记原状态 + 车辆置 REPAIRING + 审计 + 重算风险；
- resolve / close → 恢复原状态（同车仍有未结束的车辆故障异常则保持维修中）+ 清空记录列 + 审计 + 重算；
- 边界：订单没有车辆时跳过；非车辆故障类型不动车辆。

fixture 里 bootstrap 车辆（津A·12345）初始状态是 IN_TRANSIT，等价于"派车在途"的真实起点。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.models import AuditLog
from app.models.enums import ExceptionStatus
from app.models.exception import ExceptionCase
from app.models.master import Vehicle
from app.repositories import Repos
from app.rules import state_machine
from app.services.common import apply_transition, bump_version
from app.services.exceptions import ExceptionService

ORDERS = "/api/v1/orders"
EXCEPTIONS = "/api/v1/exceptions"
APPLIED = "exception.vehicle_repairing_applied"
REVERTED = "exception.vehicle_repairing_reverted"


# --- 脚手架 ---------------------------------------------------------------
def _new_order(client, headers, bootstrap, *, admin_headers=None) -> dict:
    write_headers = admin_headers or headers
    return client.post(
        ORDERS,
        headers=write_headers,
        json={
            "customer_id": bootstrap["customers"]["vip"].id,
            "origin_city": "天津",
            "dest_city": "上海",
            "distance_km": 200,
        },
    ).json()


def _order_with_vehicle(client, headers, bootstrap, *, admin_headers=None) -> dict:
    """建单 → 派车到 bootstrap 的车辆（该车 fixture 状态 IN_TRANSIT）。"""
    created = _new_order(client, headers, bootstrap, admin_headers=admin_headers)
    patched = client.patch(
        f"{ORDERS}/{created['id']}",
        headers=headers,
        json={"carrier_id": bootstrap["carrier"].id, "vehicle_id": bootstrap["vehicle"].id},
    )
    assert patched.status_code == 200, patched.text
    return client.get(f"{ORDERS}/{created['id']}", headers=headers).json()


def _create_case(client, headers, bootstrap, order_id: int, *, type_: str = "VEHICLE_BREAKDOWN") -> dict:
    response = client.post(
        EXCEPTIONS,
        headers=headers,
        json={
            "order_id": order_id,
            "type": type_,
            "occurred_at": bootstrap["base_time"].isoformat(),
            "note": "车辆在济南爆胎，已联系修理厂",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _to_processing(db_session, bootstrap, case_id: int) -> None:
    """把手工建单推到 PROCESSING：resolve 只允许从 PROCESSING 进入（状态机 §8.2）。"""
    repos = Repos(db_session, workspace_id=bootstrap["workspace_id"])
    service = ExceptionService(repos)
    service.confirm(case_id, actor_id=bootstrap["users"]["OPERATOR"].id)
    case = service.get(case_id)
    apply_transition(case, state_machine.EntityKind.EXCEPTION, ExceptionStatus.ANALYZING)
    apply_transition(case, state_machine.EntityKind.EXCEPTION, ExceptionStatus.PROCESSING)
    bump_version(case)
    repos.exceptions.save(case)
    db_session.commit()


def _fresh(db_session, model, obj_id: int) -> Any:
    """结束测试会话当前的读事务后重取，确保读到接口侧刚提交的数据。"""
    db_session.commit()
    db_session.expire_all()
    return db_session.get(model, obj_id)


def _audits(db_session, action: str, case_id: int) -> list[AuditLog]:
    return list(
        db_session.scalars(
            select(AuditLog).where(AuditLog.action == action, AuditLog.resource_id == case_id)
        ).all()
    )


# --- ① 录入：车辆置维修中 + 记原状态 ---------------------------------------
def test_manual_breakdown_sets_vehicle_repairing_and_remembers_previous(
    client, db_session, bootstrap, operator_headers, admin_headers
):
    order = _order_with_vehicle(client, operator_headers, bootstrap, admin_headers=admin_headers)
    case = _create_case(client, operator_headers, bootstrap, order["id"])

    assert _fresh(db_session, Vehicle, bootstrap["vehicle"].id).status == "REPAIRING"
    stored = _fresh(db_session, ExceptionCase, case["id"])
    assert stored.vehicle_status_before == "IN_TRANSIT", "必须记下录入时的原状态，否则结束异常无从改回"

    rows = _audits(db_session, APPLIED, case["id"])
    assert rows, "缺少 exception.vehicle_repairing_applied 审计"
    assert rows[-1].before_json["vehicle_status"] == "IN_TRANSIT"
    assert rows[-1].after_json["vehicle_status"] == "REPAIRING"
    assert rows[-1].after_json["vehicle_id"] == bootstrap["vehicle"].id

    # 重算风险：车辆故障因子按"现状"计入（此时车辆确实在维修中）
    codes = {factor["code"] for factor in (stored.risk_factors_json or [])}
    assert "VEHICLE_BREAKDOWN" in codes, stored.risk_factors_json


# --- ② 结束异常（resolve）：改回原状态 --------------------------------------
def test_resolve_restores_vehicle_status_and_clears_marker(
    client, db_session, bootstrap, operator_headers, admin_headers
):
    order = _order_with_vehicle(client, operator_headers, bootstrap, admin_headers=admin_headers)
    case = _create_case(client, operator_headers, bootstrap, order["id"])
    _to_processing(db_session, bootstrap, case["id"])

    version = _fresh(db_session, ExceptionCase, case["id"]).version
    response = client.post(
        f"{EXCEPTIONS}/{case['id']}/resolve",
        headers=operator_headers,
        json={"note": "修理完成，车辆已恢复在途", "expected_version": version},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "RESOLVED"

    assert _fresh(db_session, Vehicle, bootstrap["vehicle"].id).status == "IN_TRANSIT"
    assert _fresh(db_session, ExceptionCase, case["id"]).vehicle_status_before is None

    rows = _audits(db_session, REVERTED, case["id"])
    assert rows, "缺少 exception.vehicle_repairing_reverted 审计"
    assert rows[-1].after_json["restored"] is True
    assert rows[-1].after_json["kept_repairing"] is False
    assert rows[-1].after_json["vehicle_status"] == "IN_TRANSIT"


# --- ③ 同车两张故障异常：最后一张结束才真正恢复 -----------------------------
def test_same_vehicle_two_breakdowns_restore_only_when_last_resolves(
    client, db_session, bootstrap, operator_headers, admin_headers
):
    first_order = _order_with_vehicle(client, operator_headers, bootstrap, admin_headers=admin_headers)
    second_order = _order_with_vehicle(client, operator_headers, bootstrap, admin_headers=admin_headers)
    first = _create_case(client, operator_headers, bootstrap, first_order["id"])
    second = _create_case(client, operator_headers, bootstrap, second_order["id"])

    # 第二单录入时车辆已经是维修中 → 照实记录"当时的状态"
    assert _fresh(db_session, ExceptionCase, second["id"]).vehicle_status_before == "REPAIRING"

    _to_processing(db_session, bootstrap, first["id"])
    _to_processing(db_session, bootstrap, second["id"])

    first_version = _fresh(db_session, ExceptionCase, first["id"]).version
    resolved_first = client.post(
        f"{EXCEPTIONS}/{first['id']}/resolve",
        headers=operator_headers,
        json={"note": "第一单修好，但同车还有故障异常未结束", "expected_version": first_version},
    )
    assert resolved_first.status_code == 200, resolved_first.text
    # 同车还有未结束的车辆故障异常 → 保持维修中；恢复目标（IN_TRANSIT）转交给第二张单
    assert _fresh(db_session, Vehicle, bootstrap["vehicle"].id).status == "REPAIRING"
    assert _fresh(db_session, ExceptionCase, second["id"]).vehicle_status_before == "IN_TRANSIT"
    kept = _audits(db_session, REVERTED, first["id"])[-1]
    assert kept.after_json["kept_repairing"] is True
    assert kept.after_json["restored"] is False

    second_version = _fresh(db_session, ExceptionCase, second["id"]).version
    resolved_second = client.post(
        f"{EXCEPTIONS}/{second['id']}/resolve",
        headers=operator_headers,
        json={"note": "第二单也修好了", "expected_version": second_version},
    )
    assert resolved_second.status_code == 200, resolved_second.text
    # 最后一张结束 → 车辆回到"第一次进维修前"的状态，不会永久卡在 REPAIRING
    assert _fresh(db_session, Vehicle, bootstrap["vehicle"].id).status == "IN_TRANSIT"


# --- ④ 边界：订单没有车辆 → 跳过 -------------------------------------------
def test_breakdown_without_vehicle_skips_vehicle_state(
    client, db_session, bootstrap, operator_headers, admin_headers
):
    created = _new_order(client, operator_headers, bootstrap, admin_headers=admin_headers)  # 未派车
    case = _create_case(client, operator_headers, bootstrap, created["id"])

    stored = _fresh(db_session, ExceptionCase, case["id"])
    assert stored.vehicle_id is None
    assert stored.vehicle_status_before is None
    assert _fresh(db_session, Vehicle, bootstrap["vehicle"].id).status == "IN_TRANSIT"
    assert _audits(db_session, APPLIED, case["id"]) == []


# --- ⑤ 边界：非车辆故障类型 → 不动车辆 -------------------------------------
def test_delay_risk_type_does_not_touch_vehicle(
    client, db_session, bootstrap, operator_headers, admin_headers
):
    order = _order_with_vehicle(client, operator_headers, bootstrap, admin_headers=admin_headers)
    case = _create_case(client, operator_headers, bootstrap, order["id"], type_="DELAY_RISK")

    assert _fresh(db_session, ExceptionCase, case["id"]).vehicle_status_before is None
    assert _fresh(db_session, Vehicle, bootstrap["vehicle"].id).status == "IN_TRANSIT"
    assert _audits(db_session, APPLIED, case["id"]) == []


# --- ⑥ 关闭（close）同样要改回原状态 ----------------------------------------
def test_close_restores_vehicle_status(
    client, db_session, bootstrap, operator_headers, admin_headers
):
    order = _order_with_vehicle(client, operator_headers, bootstrap, admin_headers=admin_headers)
    case = _create_case(client, operator_headers, bootstrap, order["id"])

    version = _fresh(db_session, ExceptionCase, case["id"]).version
    response = client.post(
        f"{EXCEPTIONS}/{case['id']}/close",
        headers=admin_headers,
        json={"reason_code": "INVALID", "note": "位置数据误报，关闭异常", "expected_version": version},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "CLOSED"

    assert _fresh(db_session, Vehicle, bootstrap["vehicle"].id).status == "IN_TRANSIT"
    assert _fresh(db_session, ExceptionCase, case["id"]).vehicle_status_before is None
    rows = _audits(db_session, REVERTED, case["id"])
    assert rows and rows[-1].after_json["restored"] is True
