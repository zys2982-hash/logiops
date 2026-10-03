"""审批接口（§10.3 /approvals、§11.5）：diff、EXECUTED、409 幂等/乐观锁、批量逐条结果、通知与跟进副作用。

审批单由服务层直接构造（不依赖 AI 层与 seed），再用 HTTP 客户端验证执行链。
"""

from __future__ import annotations

from datetime import timedelta

from app.core.clock import state as clock_state
from app.repositories import Repos
from app.services.exceptions import ExceptionService
from tests.unit import _support

EXCEPTIONS = "/api/v1/exceptions"
APPROVALS = "/api/v1/approvals"


def _build_approvals(db_session, bootstrap) -> dict:
    """造一条 PROCESSING 异常 + 3 张 PENDING 审批单，并提交给测试客户端可见。"""
    repos = Repos(db_session, workspace_id=bootstrap["workspace_id"])
    case = _support.detected_exception(repos, bootstrap)
    service = ExceptionService(repos)
    service.confirm(case.id, expected_version=case.version, actor_id=None)
    analysis = _support.make_analysis(repos, case, output=_support.ai_output())
    result = service.apply_analysis_result(analysis.id)
    approvals = [repos.approvals.get(approval_id) for approval_id in result["approval_ids"]]
    db_session.commit()
    return {"case": case, "analysis": analysis, "approvals": approvals}


def test_list_approvals_is_flat_array(client, db_session, bootstrap, operator_headers):
    ctx = _build_approvals(db_session, bootstrap)
    response = client.get(f"{EXCEPTIONS}/{ctx['case'].id}/approvals", headers=operator_headers)
    assert response.status_code == 200, response.text
    items = response.json()
    assert isinstance(items, list) and len(items) == 3
    assert {item["action_type"] for item in items} == {"UPDATE_ETA", "CREATE_FOLLOWUP", "SAVE_NOTICE"}
    for item in items:
        assert {"id", "action_type", "status", "version"} <= set(item)
        assert item["status"] == "PENDING"


def test_approve_requires_version_and_checks_it(client, db_session, bootstrap, operator_headers):
    ctx = _build_approvals(db_session, bootstrap)
    approval = next(item for item in ctx["approvals"] if item.action_type == "UPDATE_ETA")

    missing = client.post(f"{APPROVALS}/{approval.id}/approve", headers=operator_headers, json={})
    assert missing.status_code == 422

    stale = client.post(
        f"{APPROVALS}/{approval.id}/approve",
        headers=operator_headers,
        json={"expected_version": approval.version + 3, "final_payload": {}},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "OPTIMISTIC_LOCK_CONFLICT"


def test_approve_executes_and_records_diff(client, db_session, bootstrap, operator_headers):
    ctx = _build_approvals(db_session, bootstrap)
    approval = next(item for item in ctx["approvals"] if item.action_type == "UPDATE_ETA")
    order_id = ctx["case"].order_id
    before = client.get(f"/api/v1/orders/{order_id}", headers=operator_headers).json()

    new_eta = (bootstrap["base_time"] + timedelta(hours=6)).isoformat()
    response = client.post(
        f"{APPROVALS}/{approval.id}/approve",
        headers=operator_headers,
        json={
            "expected_version": approval.version,
            "final_payload": {"eta_at": new_eta, "reason": "承运商已确认"},
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "EXECUTED"
    assert body["diff"]["changed"]
    assert "reason" in body["diff"]["changed"]
    assert body["execution_result"]["updated_fields"] == ["current_eta_at"]

    after = client.get(f"/api/v1/orders/{order_id}", headers=operator_headers).json()
    assert after["current_eta_at"] != before["current_eta_at"]

    # 已决策 → 409；驳回同理
    again = client.post(
        f"{APPROVALS}/{approval.id}/approve",
        headers=operator_headers,
        json={"expected_version": approval.version + 1, "final_payload": {}},
    )
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "APPROVAL_ALREADY_DECIDED"

    assert (
        client.post(
            f"{APPROVALS}/{approval.id}/reject",
            headers=operator_headers,
            json={"expected_version": approval.version + 1, "reason": "晚了"},
        ).status_code
        == 409
    )
    # 只有 FAILED 可以 execute 重试
    assert client.post(f"{APPROVALS}/{approval.id}/execute", headers=operator_headers).status_code == 409


def test_reject_requires_reason_and_has_no_side_effects(client, db_session, bootstrap, operator_headers):
    ctx = _build_approvals(db_session, bootstrap)
    approval = next(item for item in ctx["approvals"] if item.action_type == "SAVE_NOTICE")

    no_reason = client.post(
        f"{APPROVALS}/{approval.id}/reject",
        headers=operator_headers,
        json={"expected_version": approval.version},
    )
    assert no_reason.status_code == 422

    response = client.post(
        f"{APPROVALS}/{approval.id}/reject",
        headers=operator_headers,
        json={"expected_version": approval.version, "reason": "语气不合适"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "REJECTED"
    assert response.json()["reject_reason"] == "语气不合适"
    notifications = client.get(f"{EXCEPTIONS}/{ctx['case'].id}/notifications", headers=operator_headers).json()
    assert notifications == []


def test_approve_creates_notification_and_followup_then_edits(client, db_session, bootstrap, operator_headers):
    ctx = _build_approvals(db_session, bootstrap)
    notice = next(item for item in ctx["approvals"] if item.action_type == "SAVE_NOTICE")
    followup = next(item for item in ctx["approvals"] if item.action_type == "CREATE_FOLLOWUP")

    notice_result = client.post(
        f"{APPROVALS}/{notice.id}/approve",
        headers=operator_headers,
        json={"expected_version": notice.version},
    )
    assert notice_result.status_code == 200, notice_result.text
    assert notice_result.json()["execution_result"]["status"] == "DRAFT"

    notifications = client.get(f"{EXCEPTIONS}/{ctx['case'].id}/notifications", headers=operator_headers)
    assert notifications.status_code == 200
    items = notifications.json()
    assert isinstance(items, list) and items
    notification = items[0]
    assert notification["ai_draft_content"]

    edited = client.patch(
        f"/api/v1/notifications/{notification['id']}",
        headers=operator_headers,
        json={"expected_version": notification["version"], "content": "尊敬的客户，货物将晚到，请谅解。"},
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["ai_draft_content"] == notification["ai_draft_content"]

    sent = client.post(
        f"/api/v1/notifications/{notification['id']}/mark-sent",
        headers=operator_headers,
        json={"expected_version": edited.json()["version"]},
    )
    assert sent.status_code == 200
    assert sent.json()["status"] == "SENT_MOCK"
    assert sent.json()["sent_at"] is not None

    # 已发送不能再编辑/跳过
    assert (
        client.patch(
            f"/api/v1/notifications/{notification['id']}",
            headers=operator_headers,
            json={"expected_version": sent.json()["version"], "content": "改"},
        ).status_code
        == 409
    )
    assert (
        client.post(
            f"/api/v1/notifications/{notification['id']}/skip",
            headers=operator_headers,
            json={"expected_version": sent.json()["version"], "reason": "不用发"},
        ).status_code
        == 409
    )
    assert client.get(f"/api/v1/notifications/{notification['id']}", headers=operator_headers).status_code == 200

    followup_result = client.post(
        f"{APPROVALS}/{followup.id}/approve",
        headers=operator_headers,
        json={"expected_version": followup.version},
    )
    assert followup_result.status_code == 200, followup_result.text
    followups = client.get(f"{EXCEPTIONS}/{ctx['case'].id}/followups", headers=operator_headers).json()
    assert followups["total"] == 1
    task = followups["items"][0]
    assert task["source"] == "AI_SUGGESTED"

    done = client.patch(
        f"/api/v1/followups/{task['id']}",
        headers=operator_headers,
        json={"expected_version": task["version"], "status": "DONE"},
    )
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "DONE"
    assert done.json()["done_at"] is not None
    assert done.json()["done_by"] == bootstrap["users"]["OPERATOR"].id


def test_batch_approve_reports_each_item(client, db_session, bootstrap, operator_headers):
    ctx = _build_approvals(db_session, bootstrap)
    ids = [approval.id for approval in ctx["approvals"]]

    response = client.post(
        f"{APPROVALS}/batch-approve",
        headers=operator_headers,
        json={"exception_id": ctx["case"].id, "approval_ids": [*ids, 999999], "auto_execute": True},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == len(ids) + 1
    statuses = [item["status"] for item in body["items"]]
    assert statuses[: len(ids)] == ["EXECUTED"] * len(ids), body
    assert statuses[-1] == "NOT_FOUND"

    # 批量批准后副作用齐备
    assert client.get(f"{EXCEPTIONS}/{ctx['case'].id}/notifications", headers=operator_headers).json()
    assert client.get(f"{EXCEPTIONS}/{ctx['case'].id}/followups", headers=operator_headers).json()["total"] == 1


def test_batch_approve_without_execute_only_decides(client, db_session, bootstrap, operator_headers):
    ctx = _build_approvals(db_session, bootstrap)
    ids = [approval.id for approval in ctx["approvals"]]
    response = client.post(
        f"{APPROVALS}/batch-approve",
        headers=operator_headers,
        json={"exception_id": ctx["case"].id, "approval_ids": ids, "auto_execute": False},
    )
    assert response.status_code == 200
    assert {item["status"] for item in response.json()["items"]} == {"APPROVED"}
    assert client.get(f"{EXCEPTIONS}/{ctx['case'].id}/notifications", headers=operator_headers).json() == []


def test_viewer_cannot_decide_approvals(client, db_session, bootstrap, operator_headers, viewer_headers):
    ctx = _build_approvals(db_session, bootstrap)
    approval = ctx["approvals"][0]
    assert (
        client.post(
            f"{APPROVALS}/{approval.id}/approve",
            headers=viewer_headers,
            json={"expected_version": approval.version},
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"{APPROVALS}/{approval.id}/reject",
            headers=viewer_headers,
            json={"expected_version": approval.version, "reason": "x"},
        ).status_code
        == 403
    )
    assert client.get(f"{EXCEPTIONS}/{ctx['case'].id}/approvals", headers=viewer_headers).status_code == 200


def test_cross_tenant_approval_returns_404(client, db_session, bootstrap, operator_headers):
    _build_approvals(db_session, bootstrap)
    # 换一个不属于当前工作区的审批单 id
    assert client.get(f"{APPROVALS}/999999", headers=operator_headers).status_code == 404
    assert (
        client.post(
            f"{APPROVALS}/999999/approve",
            headers=operator_headers,
            json={"expected_version": 1},
        ).status_code
        == 404
    )


def test_expired_approval_cannot_be_approved(client, db_session, bootstrap, operator_headers):
    ctx = _build_approvals(db_session, bootstrap)
    approval = ctx["approvals"][0]
    clock_state.advance(24 * 60 + 5)
    db_session.commit()
    response = client.post(
        f"{APPROVALS}/{approval.id}/approve",
        headers=operator_headers,
        json={"expected_version": approval.version},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "APPROVAL_ALREADY_DECIDED"
    assert response.json()["error"]["details"]["status"] == "EXPIRED"
