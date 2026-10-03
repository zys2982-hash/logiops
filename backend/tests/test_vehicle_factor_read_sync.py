"""读取时自愈：异常列表/详情按订单车辆的**现状**刷新「车辆故障」因子（用户口径）。

背景：车辆状态有 5+ 个写入口（派车 / 维修 / 送达 / 异常驱动 / 消息解析），逐个挂钩容易漏，
所以把"按现状计算"放在读取路径（serializers.exception_brief + ExceptionService.detail）。
三条真机断言在这里固化成回归用例：

① 车辆改「维修中」→ 异常卡出现「车辆故障」因子（且真的落库）
② 车辆改回「空闲」→ 因子消失（详情页同样）
③ 已结束（CLOSED）的异常不受影响：保持历史判定，一个字段都不改

fixture 里 bootstrap 车辆（津A·12345）初始状态 IN_TRANSIT（等价"派车在途"）。
"""

from __future__ import annotations

from app.models.exception import ExceptionCase
from app.models.master import Vehicle
from app.repositories import Repos
from app.services.exceptions import ExceptionService

ORDERS = "/api/v1/orders"
EXCEPTIONS = "/api/v1/exceptions"
FACTOR = "VEHICLE_BREAKDOWN"


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
    created = _new_order(client, headers, bootstrap, admin_headers=admin_headers)
    patched = client.patch(
        f"{ORDERS}/{created['id']}",
        headers=headers,
        json={"carrier_id": bootstrap["carrier"].id, "vehicle_id": bootstrap["vehicle"].id},
    )
    assert patched.status_code == 200, patched.text
    return client.get(f"{ORDERS}/{created['id']}", headers=headers).json()


def _create_case(client, headers, bootstrap, order_id: int) -> dict:
    response = client.post(
        EXCEPTIONS,
        headers=headers,
        json={
            "order_id": order_id,
            "type": "VEHICLE_BREAKDOWN",
            "occurred_at": bootstrap["base_time"].isoformat(),
            "note": "车辆在济南爆胎，已联系修理厂",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _set_vehicle_status(db_session, vehicle_id: int, status: str) -> None:
    vehicle = db_session.get(Vehicle, vehicle_id)
    assert vehicle is not None
    vehicle.status = status
    db_session.commit()


def _set_case_factors(db_session, case_id: int, factors: list[dict]) -> None:
    case = db_session.get(ExceptionCase, case_id)
    assert case is not None
    case.risk_factors_json = factors
    db_session.commit()


def _fresh(db_session, model, obj_id: int):
    """结束测试会话当前的读事务后重取，确保读到接口侧刚提交的数据。"""
    db_session.commit()
    db_session.expire_all()
    return db_session.get(model, obj_id)


def _codes(payload: dict) -> set[str]:
    return {str(factor.get("code")) for factor in (payload.get("risk_factors") or []) if isinstance(factor, dict)}


def _card(client, headers, case_id: int) -> dict:
    """异常中心列表里那张卡（列表走 serializers.exception_brief）。"""
    response = client.get(EXCEPTIONS, headers=headers)
    assert response.status_code == 200, response.text
    cards = {item["id"]: item for item in response.json()["items"]}
    assert case_id in cards, "新建的异常应当出现在列表里"
    return cards[case_id]


def _detail(client, headers, case_id: int) -> dict:
    """异常详情（走 ExceptionService.detail，与列表是两个入口，都要按现状显示）。"""
    response = client.get(f"{EXCEPTIONS}/{case_id}", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def _to_processing(db_session, bootstrap, case_id: int) -> None:
    """4 状态模型：确认即进入处理中。"""
    repos = Repos(db_session, workspace_id=bootstrap["workspace_id"])
    service = ExceptionService(repos)
    service.confirm(case_id, actor_id=bootstrap["users"]["OPERATOR"].id)
    db_session.commit()


def _strip_factor(factors: list[dict] | None) -> list[dict]:
    return [f for f in (factors or []) if str(f.get("code")) != FACTOR]


# --- ① 车辆进入「维修中」→ 卡片出现「车辆故障」 -----------------------------
def test_list_read_sync_adds_factor_when_vehicle_becomes_repairing(
    client, db_session, bootstrap, operator_headers, admin_headers
):
    order = _order_with_vehicle(client, operator_headers, bootstrap, admin_headers=admin_headers)
    case = _create_case(client, operator_headers, bootstrap, order["id"])

    # 起点：车辆空闲且因子未计（录入路径会把车置维修中，这里人工拉平成一致态）
    _set_vehicle_status(db_session, bootstrap["vehicle"].id, "IDLE")
    stored = _fresh(db_session, ExceptionCase, case["id"])
    _set_case_factors(db_session, case["id"], _strip_factor(stored.risk_factors_json))

    # 一致态读一次：不应触发任何写入（避免每次 GET 都写库）
    version_before = _fresh(db_session, ExceptionCase, case["id"]).version
    assert FACTOR not in _codes(_card(client, operator_headers, case["id"]))
    assert _fresh(db_session, ExceptionCase, case["id"]).version == version_before, "一致时不该重算"

    # 用户把车辆改成「维修中」→ 下次打开异常中心（列表）因子自动出现
    _set_vehicle_status(db_session, bootstrap["vehicle"].id, "REPAIRING")
    card = _card(client, operator_headers, case["id"])
    assert FACTOR in _codes(card), card["risk_factors"]
    assert any(f.get("label") == "车辆故障" for f in card["risk_factors"])

    # 自愈必须真的落库（GET 的请求级事务提交），不是只在响应里临时算
    persisted = _fresh(db_session, ExceptionCase, case["id"])
    assert FACTOR in {str(f.get("code")) for f in (persisted.risk_factors_json or [])}


# --- ② 车辆改回「空闲」→ 因子消失（列表与详情都对） ------------------------
def test_read_sync_removes_factor_when_vehicle_leaves_repairing(
    client, db_session, bootstrap, operator_headers, admin_headers
):
    order = _order_with_vehicle(client, operator_headers, bootstrap, admin_headers=admin_headers)
    case = _create_case(client, operator_headers, bootstrap, order["id"])
    _set_vehicle_status(db_session, bootstrap["vehicle"].id, "REPAIRING")

    # 维修中：列表与详情都应有「车辆故障」
    assert FACTOR in _codes(_card(client, operator_headers, case["id"]))
    assert FACTOR in _codes(_detail(client, operator_headers, case["id"]))

    # 车辆修好回到「空闲」→ 因子消失（界面显示"现状"而不是建单快照）
    _set_vehicle_status(db_session, bootstrap["vehicle"].id, "IDLE")
    card = _card(client, operator_headers, case["id"])
    assert FACTOR not in _codes(card), card["risk_factors"]
    assert FACTOR not in _codes(_detail(client, operator_headers, case["id"]))

    persisted = _fresh(db_session, ExceptionCase, case["id"])
    assert FACTOR not in {str(f.get("code")) for f in (persisted.risk_factors_json or [])}


# --- ③ 已结束的异常不受影响：历史判定原样保留 -------------------------------
def test_ended_case_keeps_historical_factors(
    client, db_session, bootstrap, operator_headers, admin_headers
):
    order = _order_with_vehicle(client, operator_headers, bootstrap, admin_headers=admin_headers)
    case = _create_case(client, operator_headers, bootstrap, order["id"])
    _to_processing(db_session, bootstrap, case["id"])

    version = _fresh(db_session, ExceptionCase, case["id"]).version
    closed = client.post(
        f"{EXCEPTIONS}/{case['id']}/close",
        headers=admin_headers,
        json={"reason_code": "INVALID", "note": "位置数据误报，关闭异常", "expected_version": version},
    )
    assert closed.status_code == 200, closed.text
    assert closed.json()["status"] == "CLOSED"

    # 场景 A：已结束的单里存着「车辆故障」因子，之后车辆空闲 → 历史判定不得被抹掉
    stored = _fresh(db_session, ExceptionCase, case["id"])
    historical_factor = {"code": FACTOR, "label": "车辆故障", "weight": 1, "detail": "历史判定"}
    _set_case_factors(
        db_session, case["id"], [*_strip_factor(stored.risk_factors_json), historical_factor]
    )
    _set_vehicle_status(db_session, bootstrap["vehicle"].id, "IDLE")
    snapshot = _fresh(db_session, ExceptionCase, case["id"])
    before = (snapshot.level, snapshot.risk_score, snapshot.risk_factors_json, snapshot.version)

    card = _card(client, operator_headers, case["id"])
    assert FACTOR in _codes(card), "已结束的异常应保持历史判定，不能因车辆现状改写"
    after = _fresh(db_session, ExceptionCase, case["id"])
    assert (after.level, after.risk_score, after.risk_factors_json, after.version) == before

    # 场景 B：已结束的单没有该因子，之后车辆又变维修中 → 也不许补进来
    _set_case_factors(db_session, case["id"], _strip_factor(after.risk_factors_json))
    _set_vehicle_status(db_session, bootstrap["vehicle"].id, "REPAIRING")
    snapshot = _fresh(db_session, ExceptionCase, case["id"])
    before = (snapshot.level, snapshot.risk_score, snapshot.risk_factors_json, snapshot.version)

    assert FACTOR not in _codes(_card(client, operator_headers, case["id"]))
    after = _fresh(db_session, ExceptionCase, case["id"])
    assert (after.level, after.risk_score, after.risk_factors_json, after.version) == before


# --- 边界：非车辆故障类型 / 订单没有车辆 → 永不改因子 -----------------------
def test_non_breakdown_and_vehicleless_cases_are_untouched(
    client, db_session, bootstrap, operator_headers, admin_headers
):
    order = _order_with_vehicle(client, operator_headers, bootstrap, admin_headers=admin_headers)
    response = client.post(
        EXCEPTIONS,
        headers=operator_headers,
        json={
            "order_id": order["id"],
            "type": "DELAY_RISK",
            "occurred_at": bootstrap["base_time"].isoformat(),
            "note": "预计延误",
        },
    )
    assert response.status_code == 201, response.text
    delay_case = response.json()

    _set_vehicle_status(db_session, bootstrap["vehicle"].id, "REPAIRING")
    snapshot = _fresh(db_session, ExceptionCase, delay_case["id"])
    before = (snapshot.level, snapshot.risk_score, snapshot.risk_factors_json, snapshot.version)
    _card(client, operator_headers, delay_case["id"])
    after = _fresh(db_session, ExceptionCase, delay_case["id"])
    assert (after.level, after.risk_score, after.risk_factors_json, after.version) == before

    # 订单没有车辆：车辆故障异常也不该被"现状"改写（vehicle_id 为空 → 非维修中）
    plain = _new_order(client, operator_headers, bootstrap, admin_headers=admin_headers)
    no_vehicle = _create_case(client, operator_headers, bootstrap, plain["id"])
    snapshot = _fresh(db_session, ExceptionCase, no_vehicle["id"])
    before = (snapshot.level, snapshot.risk_score, snapshot.risk_factors_json, snapshot.version)
    _card(client, operator_headers, no_vehicle["id"])
    after = _fresh(db_session, ExceptionCase, no_vehicle["id"])
    assert (after.level, after.risk_score, after.risk_factors_json, after.version) == before


# --- 幂等：列表 + 详情连续读，第二次不再重算（版本不动） --------------------
def test_read_sync_is_idempotent(
    client, db_session, bootstrap, operator_headers, admin_headers
):
    order = _order_with_vehicle(client, operator_headers, bootstrap, admin_headers=admin_headers)
    case = _create_case(client, operator_headers, bootstrap, order["id"])
    _set_vehicle_status(db_session, bootstrap["vehicle"].id, "IDLE")
    stored = _fresh(db_session, ExceptionCase, case["id"])
    _set_case_factors(db_session, case["id"], _strip_factor(stored.risk_factors_json))

    _set_vehicle_status(db_session, bootstrap["vehicle"].id, "REPAIRING")
    first = _card(client, operator_headers, case["id"])
    assert FACTOR in _codes(first)
    version_after_heal = _fresh(db_session, ExceptionCase, case["id"]).version

    _card(client, operator_headers, case["id"])
    _detail(client, operator_headers, case["id"])
    assert _fresh(db_session, ExceptionCase, case["id"]).version == version_after_heal, "第二次读不该再重算"
