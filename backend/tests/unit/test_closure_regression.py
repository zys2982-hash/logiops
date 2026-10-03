"""到站即送达 + CASE-A 主案例闭环回归（Lead 验收口径；§8.1 / §8.2 / §13.3）。

覆盖一键验收暴露的真缺陷：
- 模拟轨迹推进到终点后，订单必须 DELIVERED、异常不得继续搁浅在 PROCESSING；
- tick 重算必须保留"维修等待"口径，不得把 CRITICAL 错误降档；
- advance-to-less 的目标必须是确定性最早未关闭异常（CASE-A），终态 CLOSED + closed_at 非空。
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


def test_order_delivered_resolves_open_exception(client, db_session, bootstrap, operator_headers):
    """PROCESSING 异常 + 大幅 tick 覆盖到终点 → 订单 DELIVERED、异常 RESOLVED、resolved_at 非空。"""
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

    exception = client.get(f"{EXCEPTIONS}/{case.id}", headers=operator_headers).json()
    assert exception["status"] == str(ExceptionStatus.RESOLVED), exception
    assert exception["resolved_at"] is not None
    # 规则定级不被"到站即送达"抹掉：送达时刻已晚于承诺 → 仍为违约
    assert exception["sla_breached"] is True
    assert exception["level"] == "CRITICAL"
    assert exception["risk_score"] == 4


def test_big_tick_never_strands_case_a(client, db_session, bootstrap, operator_headers):
    """未人工处置的 DETECTED/PROCESSING 异常，到站后也必须闭环（不能挂着不结束）。"""
    _seed(db_session, bootstrap)
    order, case_item = _case_a(client, operator_headers)

    tick = client.post("/api/v1/demo/actions/tick", headers=operator_headers, json={"minutes": 3000})
    assert tick.status_code == 200, tick.text

    assert client.get(f"{ORDERS}/{order['id']}", headers=operator_headers).json()["status"] == "DELIVERED"
    exception = client.get(f"{EXCEPTIONS}/{case_item['id']}", headers=operator_headers).json()
    assert exception["status"] in {str(ExceptionStatus.RESOLVED), str(ExceptionStatus.CLOSED)}, exception
    assert exception["resolved_at"] or exception["closed_at"], exception


def test_tick_keeps_repair_wait_and_does_not_downgrade(client, db_session, bootstrap, operator_headers):
    """维修等待中的订单：小步长 tick 不得把 ETA 算成"立刻起程"从而错误降档。"""
    _seed(db_session, bootstrap)
    _, case_item = _case_a(client, operator_headers)
    assert case_item["level"] == "CRITICAL"
    assert case_item["risk_score"] == 4

    tick = client.post("/api/v1/demo/actions/tick", headers=operator_headers, json={"minutes": 60})
    assert tick.status_code == 200, tick.text

    exception = client.get(f"{EXCEPTIONS}/{case_item['id']}", headers=operator_headers).json()
    assert exception["status"] == str(ExceptionStatus.DETECTED)  # 机器提议、人确认，不自动推进也不降档
    # 机器提议阶段不允许"高危单静默降档"：等级/风险分保持，SLA 数字仍按事实刷新
    assert exception["level"] == "CRITICAL", exception
    assert exception["risk_score"] == 4
    assert exception["expected_eta_at"] is not None


def test_advance_to_less_closes_case_a(client, db_session, bootstrap, operator_headers):
    """POST /demo/actions/advance-to-less：目标必须是 CASE-A，终态 CLOSED 且 closed_at 非空。"""
    _seed(db_session, bootstrap)
    order, case_item = _case_a(client, operator_headers)

    response = client.post("/api/v1/demo/actions/advance-to-less", headers=operator_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["target_order_no"] == CASE_A_ORDER_NO, body
    assert body["target_exception_no"] == CASE_A_CASE_NO, body
    assert body["final_order_status"] == str(OrderStatus.CLOSED), body
    assert body["final_exception_status"] == str(ExceptionStatus.CLOSED), body
    assert body["stopped_reason"] == "TARGET_CLOSED", body

    exception = client.get(f"{EXCEPTIONS}/{case_item['id']}", headers=operator_headers).json()
    assert exception["status"] == str(ExceptionStatus.CLOSED)
    assert exception["closed_at"] is not None
    assert client.get(f"{ORDERS}/{order['id']}", headers=operator_headers).json()["status"] == str(
        OrderStatus.CLOSED
    )

    # 幂等：再推一次不报错
    again = client.post("/api/v1/demo/actions/advance-to-less", headers=operator_headers)
    assert again.status_code == 200
    assert again.json()["ticks"] == 0
