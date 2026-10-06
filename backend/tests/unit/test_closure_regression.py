"""到站即送达 + CASE-A 主案例闭环回归（§8.1 / §8.2 / §13.3；收口口径 2026-10-06）。

覆盖一键验收暴露的真缺陷 + 收口口径：
- 模拟轨迹推进到终点后，订单必须 DELIVERED；
- **异常不随送达 / 超时自动收口**（用户口径：解决 / 关闭只由人工点），到站后仍开着等人工；
- 人工点「解决」后，"结束那一刻"的风险快照必须保留（不得被刷成空因子 / LOW 0 分）；
- tick 重算必须保留"维修等待"口径，不得把 CRITICAL 错误降档。
"""

from __future__ import annotations

from app.models.enums import ExceptionStatus, OrderStatus
from app.repositories import Repos
from app.services.exceptions import ExceptionService
from tests.unit import _support

CASE_A_ORDER_NO = "SO20260930021"
CASE_A_CASE_NO = "EX20260930001"
ORDERS = "/api/v1/orders"
EXCEPTIONS = "/api/v1/exceptions"


def _seed(db_session, bootstrap) -> dict:
    from app.seed import reset_demo_data

    summary = reset_demo_data(db_session, workspace_id=bootstrap["workspace_id"], scenario="case-a")
    db_session.commit()
    return summary


def _case_a(client, headers) -> tuple[dict, dict]:
    order = client.get(ORDERS, headers=headers, params={"order_no": CASE_A_ORDER_NO}).json()["items"][0]
    cases = client.get(f"{ORDERS}/{order['id']}/exceptions", headers=headers).json()
    assert cases, "seed 未产出 CASE-A 关联异常"
    return order, cases[0]


def test_delivery_keeps_exception_open_until_manual_resolve(
    client, db_session, bootstrap, operator_headers
):
    """PROCESSING 异常 + 大幅 tick 覆盖到终点 → 订单 DELIVERED，异常**仍处理中**（等人工）。

    人工点「解决」之后才结束，并要求"结束那一刻"的车况/风险快照留档正确。
    """
    _seed(db_session, bootstrap)
    order, case_item = _case_a(client, operator_headers)

    repos = Repos(db_session, workspace_id=bootstrap["workspace_id"])
    case = repos.exceptions.get(case_item["id"])
    service = ExceptionService(repos)
    # 4 状态模型：seed 的 CASE-A 直接落在"处理中"（旧数据可能是 DETECTED/CONFIRMING，先确认推进）
    if case.status in {str(ExceptionStatus.DETECTED), str(ExceptionStatus.CONFIRMING)}:
        service.confirm(case.id, expected_version=case.version, actor_id=None)
    analysis = _support.make_analysis(repos, case, output=_support.ai_output())
    service.apply_analysis_result(analysis.id)
    db_session.commit()
    assert case.status == str(ExceptionStatus.PROCESSING)

    tick = client.post("/api/v1/demo/actions/tick", headers=operator_headers, json={"minutes": 3000})
    assert tick.status_code == 200, tick.text

    detail = client.get(f"{ORDERS}/{order['id']}", headers=operator_headers).json()
    assert detail["status"] == str(OrderStatus.DELIVERED), detail
    assert detail["delivered_at"] is not None

    # 送达**不再自动收口**：单子还开着，等你处置
    exception = client.get(f"{EXCEPTIONS}/{case.id}", headers=operator_headers).json()
    assert exception["status"] == str(ExceptionStatus.PROCESSING), exception
    assert exception["resolved_at"] is None and exception["closed_at"] is None
    # 车辆故障单不做 SLA 判定（2026-10-05）：送达时刻的"承诺/预计"只是订单事实，不进异常风险
    assert exception["sla_breached"] is False
    assert exception["sla_delay_minutes"] is None

    # 人工点「解决」→ 才结束；"结束那一刻"车已修好（回在途）→ 只剩 VIP 1 分 → MEDIUM 留档
    resolved = client.post(
        f"{EXCEPTIONS}/{case.id}/resolve",
        headers=operator_headers,
        json={"note": "车已修好，货也送到了", "expected_version": exception["version"]},
    )
    assert resolved.status_code == 200, resolved.text
    archived = resolved.json()
    assert archived["status"] == str(ExceptionStatus.RESOLVED), archived
    assert archived["resolved_at"] is not None
    assert archived["level"] == "MEDIUM"
    assert archived["risk_score"] == 1
    assert [f["code"] for f in archived["risk_factors"]] == ["CUSTOMER_VIP"]


def test_big_tick_keeps_case_open_for_manual_handling(client, db_session, bootstrap, operator_headers):
    """未人工处置的异常：到站后**不**闭环，且不得留下任何程序自动关闭的痕迹。"""
    _seed(db_session, bootstrap)
    order, case_item = _case_a(client, operator_headers)

    tick = client.post("/api/v1/demo/actions/tick", headers=operator_headers, json={"minutes": 3000})
    assert tick.status_code == 200, tick.text

    assert client.get(f"{ORDERS}/{order['id']}", headers=operator_headers).json()["status"] == "DELIVERED"
    exception = client.get(f"{EXCEPTIONS}/{case_item['id']}", headers=operator_headers).json()
    assert exception["status"] in {str(ExceptionStatus.DETECTED), str(ExceptionStatus.PROCESSING)}, exception
    assert exception["resolved_at"] is None and exception["closed_at"] is None
    events = client.get(f"{EXCEPTIONS}/{case_item['id']}/events", headers=operator_headers).json()["items"]
    assert not [event for event in events if event["event_type"] == "CLOSED"], events


def test_tick_keeps_repair_wait_and_does_not_downgrade(client, db_session, bootstrap, operator_headers):
    """维修等待中的订单：小步长 tick 不得把 ETA 算成"立刻起程"从而错误降档。"""
    _seed(db_session, bootstrap)
    _, case_item = _case_a(client, operator_headers)
    # 新模型：车辆故障单 = 车辆故障 1 + VIP 1 = 2 → MEDIUM（无延误/违约加分）
    assert case_item["level"] == "MEDIUM"
    assert case_item["risk_score"] == 2

    tick = client.post("/api/v1/demo/actions/tick", headers=operator_headers, json={"minutes": 60})
    assert tick.status_code == 200, tick.text

    exception = client.get(f"{EXCEPTIONS}/{case_item['id']}", headers=operator_headers).json()
    assert exception["status"] == str(ExceptionStatus.DETECTED)  # 机器提议、人确认，不自动推进也不降档
    # 机器提议阶段不允许"高危单静默降档"：等级/风险分保持
    assert exception["level"] == "MEDIUM", exception
    assert exception["risk_score"] == 2
    assert exception["expected_eta_at"] is not None


def test_advance_to_less_closes_case_a(client, db_session, bootstrap, operator_headers):
    """POST /demo/actions/advance-to-less：目标必须是 CASE-A，并**按人工起点**显式收口（非自动）。"""
    _seed(db_session, bootstrap)
    order, case_item = _case_a(client, operator_headers)

    response = client.post("/api/v1/demo/actions/advance-to-less", headers=operator_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["target_order_no"] == CASE_A_ORDER_NO, body
    assert body["target_exception_no"] == CASE_A_CASE_NO, body
    assert body["final_order_status"] == str(OrderStatus.DELIVERED), body
    assert body["final_exception_status"] == str(ExceptionStatus.CLOSED), body
    assert body["stopped_reason"] == "TARGET_CLOSED", body

    exception = client.get(f"{EXCEPTIONS}/{case_item['id']}", headers=operator_headers).json()
    assert exception["status"] == str(ExceptionStatus.CLOSED)
    assert exception["closed_at"] is not None

    # 幂等：再推一次不报错
    again = client.post("/api/v1/demo/actions/advance-to-less", headers=operator_headers)
    assert again.status_code == 200
    assert again.json()["ticks"] == 0
