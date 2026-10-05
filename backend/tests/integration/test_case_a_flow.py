"""CASE-A 端到端验收测试（Lead 维护）：发现 → 解析 → 分析 → 审批 → 执行 → 跟进 → 送达 → 自动关闭 → 审计。

这是"项目真的闭环了"的唯一判据。依赖 seed / AI / 异常接口全部就绪；未就绪时整体 skip，
避免在并行开发期制造噪声。
"""

from __future__ import annotations

import importlib.util

import pytest

from app.core.clock import parse_dt

for module_name in ("app.seed", "app.ai.runner", "app.services.tick"):
    if importlib.util.find_spec(module_name) is None:  # pragma: no cover - 开发期
        pytest.skip(f"依赖未就绪：{module_name}", allow_module_level=True)

CASE_A_ORDER_NO = "SO20260930021"


def _order_delay_minutes(exception: dict) -> int:
    """订单层面的延误（预计到达 − 承诺到达）。

    2026-10-05 新模型：车辆故障单不做 SLA 判定（`sla_delay_minutes=None`），
    但订单的"承诺/预计"时刻仍然留档，AI 的 impact.delay_minutes 复述的是这个事实。
    """
    expected = parse_dt(exception["expected_eta_at"])
    promised = parse_dt(exception["promised_delivery_at"])
    assert expected is not None and promised is not None
    return int(round((expected - promised).total_seconds() / 60))


def _seed(db_session, workspace_id: int) -> dict:
    from app.seed import reset_demo_data

    summary = reset_demo_data(db_session, workspace_id=workspace_id, scenario="case-a")
    db_session.commit()
    return summary or {}


def _find_case_a(client, headers) -> dict:
    response = client.get("/api/v1/orders", params={"order_no": CASE_A_ORDER_NO}, headers=headers)
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert items, f"seed 未产出主案例订单 {CASE_A_ORDER_NO}"
    order = items[0]

    response = client.get(f"/api/v1/orders/{order['id']}/exceptions", headers=headers)
    assert response.status_code == 200, response.text
    exceptions = response.json()
    items = exceptions.get("items", exceptions) if isinstance(exceptions, dict) else exceptions
    assert items, "主案例订单没有关联异常"
    detail = client.get(f"/api/v1/exceptions/{items[0]['id']}", headers=headers)
    assert detail.status_code == 200, detail.text
    return {"order": order, "exception": detail.json()}


def test_case_a_full_closed_loop(client, db_session, bootstrap, operator_headers):
    _seed(db_session, bootstrap["workspace_id"])
    found = _find_case_a(client, operator_headers)
    exception_id = found["exception"]["id"]

    # 1) 检测结果（2026-10-05 新模型）：CASE-A 是**车辆故障单** → 车辆故障 1 + VIP 1 = 2 → MEDIUM；
    #    车辆单不做 SLA 判定，所以没有延误/违约数字（订单的承诺/预计时刻仍留档）
    assert found["exception"]["level"] == "MEDIUM", found["exception"]
    assert found["exception"]["risk_score"] == 2
    assert found["exception"]["sla_breached"] is False
    assert found["exception"]["sla_delay_minutes"] is None
    order_delay = _order_delay_minutes(found["exception"])
    assert 240 <= order_delay <= 300, found["exception"]

    # 2) 确认 → 触发 AI 分析
    if found["exception"]["status"] == "DETECTED":
        confirmed = client.post(f"/api/v1/exceptions/{exception_id}/confirm", headers=operator_headers)
        assert confirmed.status_code in (200, 201), confirmed.text

    analyze = client.post(f"/api/v1/exceptions/{exception_id}/analyze", headers=operator_headers)
    assert analyze.status_code == 202, analyze.text
    analysis_id = analyze.json()["analysis_id"]

    # 3) 分析结果：有工具步骤、有建议、事实与后端口径一致（replay 模式也要成立）
    analysis = client.get(f"/api/v1/ai-analyses/{analysis_id}", headers=operator_headers)
    assert analysis.status_code == 200, analysis.text
    body = analysis.json()
    assert body["status"] == "READY", body
    assert len(body.get("steps", [])) >= 3, body
    output = body["output"]
    # 等级只由规则定：车辆单 = 车辆故障 1 + VIP 1 = 2 → MEDIUM（LLM 无权改）
    assert body["risk_level_calculated"] == "MEDIUM"
    assert abs(output["impact"]["delay_minutes"] - order_delay) <= 5
    # 车辆单不做 SLA 判定 → 事实基线里 sla_breached=False，LLM 必须复述
    assert output["impact"]["sla_breached"] is False
    assert body["is_replay"] is True
    assert output["evidence_refs"], "证据引用不能为空"

    # 4) 审批：把 AI 建议的 ETA 手工改一下，验证 diff 与执行留痕
    approvals = client.get(f"/api/v1/exceptions/{exception_id}/approvals", headers=operator_headers)
    assert approvals.status_code == 200, approvals.text
    pending = [item for item in approvals.json() if item["status"] == "PENDING"]
    assert pending, "AI 建议没有生成审批单"

    eta_approval = next((item for item in pending if item["action_type"] == "UPDATE_ETA"), pending[0])
    new_eta = output["impact"].get("expected_eta_at") or found["exception"]["expected_eta_at"]
    approve = client.post(
        f"/api/v1/approvals/{eta_approval['id']}/approve",
        headers=operator_headers,
        json={"expected_version": eta_approval["version"], "final_payload": {"eta_at": new_eta, "reason": "验收测试"}},
    )
    assert approve.status_code == 200, approve.text
    assert approve.json()["status"] == "EXECUTED"

    # 5) 其余审批单批量批准
    remaining = [item["id"] for item in pending if item["id"] != eta_approval["id"]]
    if remaining:
        batch = client.post(
            "/api/v1/approvals/batch-approve",
            headers=operator_headers,
            json={"exception_id": exception_id, "approval_ids": remaining, "auto_execute": True},
        )
        assert batch.status_code == 200, batch.text

    # 6) 执行结果：ETA 更新 + 通知草稿 + 跟进任务 + 审计留痕
    after = client.get(f"/api/v1/exceptions/{exception_id}", headers=operator_headers).json()
    assert after["order"]["current_eta_at"] == new_eta
    notifications = client.get(f"/api/v1/exceptions/{exception_id}/notifications", headers=operator_headers).json()
    assert notifications, "批准 SAVE_NOTICE 后应产生通知草稿"
    timeline = client.get(f"/api/v1/exceptions/{exception_id}/events", headers=operator_headers).json()
    timeline_items = timeline.get("items", timeline) if isinstance(timeline, dict) else timeline
    event_types = {item["event_type"] for item in timeline_items}
    assert {"DETECTED", "MESSAGE_ADDED", "ANALYSIS_REQUESTED", "ANALYSIS_READY", "EXECUTED"} <= event_types

    audit = client.get(
        "/api/v1/audit-logs",
        params={"resource_type": "exception", "resource_id": exception_id},
        headers=operator_headers,
    )
    assert audit.status_code == 200 and audit.json()["total"] >= 1

    # 7) 推进演示时钟：车辆恢复 → 送达 → 异常自动关闭
    advanced = client.post("/api/v1/demo/actions/advance-to-less", headers=operator_headers)
    assert advanced.status_code == 200, advanced.text
    final = client.get(f"/api/v1/exceptions/{exception_id}", headers=operator_headers).json()
    assert final["status"] == "CLOSED", final
    assert final["closed_at"] is not None


def test_case_a_is_reproducible(client, db_session, bootstrap, operator_headers):
    """同一 seed 重复执行，关键结论完全一致（Demo 可复现的硬指标）。"""
    first = _seed(db_session, bootstrap["workspace_id"])
    one = _find_case_a(client, operator_headers)["exception"]
    second = _seed(db_session, bootstrap["workspace_id"])
    two = _find_case_a(client, operator_headers)["exception"]

    for key in ("level", "risk_score", "sla_delay_minutes", "sla_breached", "case_no"):
        assert one[key] == two[key], f"{key} 在重复 seed 后不一致：{one[key]} != {two[key]}"
    assert first.get("orders") == second.get("orders") if "orders" in first else True
