"""车辆-司机绑定契约（Lead 维护）。

项目约定（用户拍板的 MVP 取舍）：**车与司机 1:1 固定绑定，不做排班/分配表**；
换人由"车辆编辑页面"人工维护，程序只保证绑定关系**不跨承运商**。

守住四条：
1. 绑定不变量：每台车都有主驾、主驾与车辆同承运商、同一司机不被多台车绑定；
2. 创建车辆时绑别家司机 → 422；
3. 修改车辆把承运商换成别家却不换司机 → 422；
4. 显式清空主驾（current_driver_id=null）允许（不能把"清空"误判成"沿用旧司机"而误拒）。
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.master import Carrier, Driver, Vehicle


def _assert_binding_invariants(session: Session) -> None:
    vehicles = session.scalars(select(Vehicle)).all()
    assert vehicles, "测试库应有车辆数据"
    used_driver_ids: list[int] = []
    for vehicle in vehicles:
        assert vehicle.current_driver_id is not None, f"{vehicle.plate_no} 没有主驾（约定：车与司机 1:1 绑定）"
        driver = session.get(Driver, vehicle.current_driver_id)
        assert driver is not None, f"{vehicle.plate_no} 的主驾不存在"
        assert driver.carrier_id == vehicle.carrier_id, (
            f"{vehicle.plate_no} 的主驾不属于同一承运商（车辆 {vehicle.carrier_id} vs 司机 {driver.carrier_id}）"
        )
        used_driver_ids.append(driver.id)
    assert len(used_driver_ids) == len(set(used_driver_ids)), "同一司机被多台车绑定"


def _new_carrier(session: Session, workspace_id: int, code: str, name: str) -> Carrier:
    carrier = Carrier(workspace_id=workspace_id, code=code, name=name)
    session.add(carrier)
    session.flush()
    return carrier


def _vehicle_payload(carrier_id: int, driver_id: int | None = None, plate_no: str = "京Z·88888") -> dict:
    payload: dict = {"plate_no": plate_no, "carrier_id": carrier_id, "status": "IDLE"}
    if driver_id is not None:
        payload["current_driver_id"] = driver_id
    return payload


def test_binding_invariants_hold(bootstrap, db_session):
    """先把不变量钉在 fixture 数据上（后续用例会新增车辆，所以这条放在文件最前面）。"""
    _assert_binding_invariants(db_session)


def test_create_vehicle_rejects_foreign_driver(client, db_session, bootstrap, admin_headers):
    """把别家承运商的司机绑到新车上 → 422，且车辆不会被创建。"""
    other = _new_carrier(db_session, bootstrap["workspace_id"], "CR-X1", "外部承运商")
    foreign_driver = Driver(
        workspace_id=bootstrap["workspace_id"], name="外部司机", carrier_id=other.id, status="AVAILABLE"
    )
    db_session.add(foreign_driver)
    db_session.commit()

    response = client.post(
        "/api/v1/vehicles",
        headers=admin_headers,
        json=_vehicle_payload(bootstrap["carrier"].id, foreign_driver.id),
    )
    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert any("current_driver_id" in field["loc"] for field in error["details"]["fields"])
    assert db_session.scalars(select(Vehicle).where(Vehicle.plate_no == "京Z·88888")).first() is None


def test_create_vehicle_accepts_same_carrier_driver(client, db_session, bootstrap, admin_headers):
    """同承运商的司机 → 创建成功（用新司机，避免破坏"一人一车"不变量）。"""
    fresh_driver = Driver(
        workspace_id=bootstrap["workspace_id"],
        name="新司机",
        carrier_id=bootstrap["carrier"].id,
        status="AVAILABLE",
    )
    db_session.add(fresh_driver)
    db_session.commit()

    response = client.post(
        "/api/v1/vehicles",
        headers=admin_headers,
        json=_vehicle_payload(bootstrap["carrier"].id, fresh_driver.id, plate_no="京Z·66666"),
    )
    assert response.status_code in (200, 201), response.text
    body = response.json()
    assert body["carrier_id"] == bootstrap["carrier"].id
    assert body["current_driver_id"] == fresh_driver.id


def test_patch_vehicle_rejects_carrier_switch_without_driver_change(client, db_session, bootstrap, admin_headers):
    """车辆改挂到别家承运商、但保留旧主驾 → 422（否则形成跨承运商绑定）。"""
    other = _new_carrier(db_session, bootstrap["workspace_id"], "CR-X2", "另一家承运商")
    db_session.commit()

    response = client.patch(
        f"/api/v1/vehicles/{bootstrap['vehicle'].id}",
        headers=admin_headers,
        json={"carrier_id": other.id},
    )
    assert response.status_code == 422, response.text
    assert any(
        "current_driver_id" in field["loc"] for field in response.json()["error"]["details"]["fields"]
    )


def test_patch_vehicle_can_clear_driver(client, bootstrap, admin_headers):
    """显式清空主驾是允许的（model_fields_set 区分"清空"与"未传"）。"""
    response = client.patch(
        f"/api/v1/vehicles/{bootstrap['vehicle'].id}",
        headers=admin_headers,
        json={"current_driver_id": None},
    )
    assert response.status_code == 200, response.text
    assert response.json()["current_driver_id"] is None


# --- 主驾司机改为"手输姓名"（前端主用方式）：不存在 / 同名 / 已被占用都要有字段级报错 -----


def _named_payload(carrier_id: int | None, driver_name: str | None, plate_no: str) -> dict:
    payload: dict = {"plate_no": plate_no, "status": "IDLE"}
    if carrier_id is not None:
        payload["carrier_id"] = carrier_id
    if driver_name is not None:
        payload["current_driver_name"] = driver_name
    return payload


def _create_unbound_driver(session: Session, bootstrap, name: str) -> Driver:
    driver = Driver(
        workspace_id=bootstrap["workspace_id"],
        name=name,
        carrier_id=bootstrap["carrier"].id,
        status="AVAILABLE",
    )
    session.add(driver)
    session.commit()
    return driver


def test_bind_driver_by_name_success(client, db_session, bootstrap, admin_headers):
    """输入存在的司机姓名 → 绑定成功，落库的是 driver_id。"""
    driver = _create_unbound_driver(db_session, bootstrap, "手输司机甲")
    response = client.post(
        "/api/v1/vehicles",
        headers=admin_headers,
        json=_named_payload(bootstrap["carrier"].id, "手输司机甲", "京Z·60001"),
    )
    assert response.status_code in (200, 201), response.text
    assert response.json()["current_driver_id"] == driver.id


def test_bind_driver_by_unknown_name_reports_field_error(client, db_session, bootstrap, admin_headers):
    """姓名不存在 → 422，且错误定位在 current_driver_name 字段（前端在输入框下显示）。"""
    response = client.post(
        "/api/v1/vehicles",
        headers=admin_headers,
        json=_named_payload(bootstrap["carrier"].id, "查无此人", "京Z·60002"),
    )
    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["message"] == "司机不存在"
    field = error["details"]["fields"][0]
    assert field["loc"] == "current_driver_name"
    assert "查无此人" in field["msg"]
    assert db_session.scalars(select(Vehicle).where(Vehicle.plate_no == "京Z·60002")).first() is None


def test_bind_driver_name_already_bound_to_another_vehicle(client, bootstrap, admin_headers):
    """该司机已被别的车辆绑定 → 422（一人一车）。"""
    bound_name = bootstrap["driver"].name  # bootstrap 的车辆已绑定这名司机
    response = client.post(
        "/api/v1/vehicles",
        headers=admin_headers,
        json=_named_payload(bootstrap["carrier"].id, bound_name, "京Z·60003"),
    )
    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["message"] == "一名司机只能绑定一台车"
    assert "已绑定车辆" in error["details"]["fields"][0]["msg"]


def test_bind_driver_name_ambiguous(client, db_session, bootstrap, admin_headers):
    """同承运商下有两个同名司机 → 422，提示先改名（避免绑错人）。"""
    _create_unbound_driver(db_session, bootstrap, "同名司机")
    _create_unbound_driver(db_session, bootstrap, "同名司机")
    response = client.post(
        "/api/v1/vehicles",
        headers=admin_headers,
        json=_named_payload(bootstrap["carrier"].id, "同名司机", "京Z·60004"),
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["message"] == "司机姓名不唯一"


def test_bind_driver_name_requires_carrier(client, bootstrap, admin_headers):
    """没选承运商就填姓名 → 422，提示先选承运商（同名司机可能属于不同承运商）。"""
    response = client.post(
        "/api/v1/vehicles",
        headers=admin_headers,
        json=_named_payload(None, bootstrap["driver"].name, "京Z·60005"),
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"]["fields"][0]["loc"] == "carrier_id"


def test_bind_driver_name_matches_within_carrier_only(client, db_session, bootstrap, admin_headers):
    """姓名只在所选承运商内匹配：别家承运商的同名司机不会被误绑。"""
    other = _new_carrier(db_session, bootstrap["workspace_id"], "CR-X9", "别家承运商")
    db_session.add(
        Driver(workspace_id=bootstrap["workspace_id"], name="别家同名", carrier_id=other.id, status="AVAILABLE")
    )
    db_session.commit()

    response = client.post(
        "/api/v1/vehicles",
        headers=admin_headers,
        json=_named_payload(bootstrap["carrier"].id, "别家同名", "京Z·60006"),
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["message"] == "司机不存在"


def test_clear_driver_with_empty_name(client, bootstrap, admin_headers):
    """姓名传空串 = 解除绑定。"""
    response = client.patch(
        f"/api/v1/vehicles/{bootstrap['vehicle'].id}",
        headers=admin_headers,
        json={"current_driver_name": ""},
    )
    assert response.status_code == 200, response.text
    assert response.json()["current_driver_id"] is None


def test_database_unique_index_blocks_double_binding(db_session, bootstrap):
    """数据库层兜底：一个司机不能绑定第二台车（唯一索引 uq_vehicle_current_driver）。"""
    from sqlalchemy.exc import IntegrityError

    driver = _create_unbound_driver(db_session, bootstrap, "索引兜底司机")
    first = Vehicle(
        workspace_id=bootstrap["workspace_id"], plate_no="京Z·61001", carrier_id=bootstrap["carrier"].id
    )
    db_session.add(first)
    db_session.flush()
    first.current_driver_id = driver.id
    db_session.flush()

    second = Vehicle(
        workspace_id=bootstrap["workspace_id"], plate_no="京Z·61002", carrier_id=bootstrap["carrier"].id
    )
    db_session.add(second)
    db_session.flush()
    with pytest.raises(IntegrityError):
        second.current_driver_id = driver.id
        db_session.flush()
    db_session.rollback()
