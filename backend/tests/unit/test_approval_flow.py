"""审批执行链（§11.5）：diff 留痕、按 action_type 分发、幂等、拒绝无副作用、过期不执行、批量部分失败。"""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.core.clock import state as clock_state
from app.core.errors import AppError, ErrorCode
from app.models.enums import ApprovalStatus, ExceptionStatus, NotificationStatus
from app.services import ai_bridge
from app.services.approvals import ApprovalExecutor, build_approvals_from_analysis, diff_payload
from app.services.common import apply_transition
from app.services.exceptions import ExceptionService
from tests.unit import _support


def test_diff_payload_lists_changed_fields():
    diff = diff_payload({"eta_at": "A", "reason": "r"}, {"eta_at": "B", "reason": "r"})
    assert diff["changed"] == ["eta_at"]
    assert diff["fields"]["eta_at"] == {"ai": "A", "final": "B"}
    assert diff["ai_value"] == {"eta_at": "A"}
    assert diff["final_value"] == {"eta_at": "B"}
    assert diff["ai_payload"] == {"eta_at": "A", "reason": "r"}


def test_build_approvals_from_suggestions_is_idempotent(db_session, bootstrap):
    repos = _support.repos_for(db_session, bootstrap)
    case = _support.detected_exception(repos, bootstrap)
    analysis = _support.make_analysis(repos, case, output=_support.ai_output())

    created = build_approvals_from_analysis(repos, analysis)
    assert [approval.action_type for approval in created] == [
        "UPDATE_ETA",
        "CREATE_FOLLOWUP",
        "SAVE_NOTICE",
    ]
    assert all(approval.status == str(ApprovalStatus.PENDING) for approval in created)
    assert all(approval.expires_at is not None for approval in created)
    # 建议 ETA 来自规则算出的 expected_eta_at（不是 LLM 编的）
    assert created[0].ai_payload_json["eta_at"] == case.expected_eta_at.isoformat() + "Z"
    assert created[0].ai_payload_json["reason"]

    # 已有 PENDING 时再次构建不重复建单（幂等）
    assert build_approvals_from_analysis(repos, analysis) == []
    assert len(repos.approvals.list_for_case(case.id)) == 3


def test_approve_executes_update_eta_with_diff(db_session, bootstrap):
    repos = _support.repos_for(db_session, bootstrap)
    case = _support.detected_exception(repos, bootstrap)
    analysis = _support.make_analysis(repos, case, output=_support.ai_output())
    approval = build_approvals_from_analysis(repos, analysis)[0]
    order = repos.orders.get(case.order_id)

    new_eta = (bootstrap["base_time"] + timedelta(hours=3)).isoformat()
    result = ApprovalExecutor(repos).approve(
        approval.id,
        actor_id=bootstrap["users"]["OPERATOR"].id,
        final_payload={"eta_at": new_eta, "reason": "验收测试"},
        expected_version=approval.version,
    )
    assert result["status"] == str(ApprovalStatus.EXECUTED)
    assert set(result["diff"]["changed"]) == {"eta_at", "reason"}
    assert result["diff"]["final_value"]["reason"] == "验收测试"
    assert result["execution_result"]["updated_fields"] == ["current_eta_at"]
    assert result["execution_result"]["order_id"] == order.id
    assert approval.status == str(ApprovalStatus.EXECUTED)
    assert approval.executed_at is not None
    assert approval.final_payload_json["reason"] == "验收测试"
    assert repos.orders.get(order.id).current_eta_at is not None

    # 审计 source=APPROVED_AI + 时间线 EXECUTED 都留痕
    audit_rows, _ = repos.audit.list(page=1, page_size=50)
    assert any(row.source == "APPROVED_AI" and row.action == "approval.executed" for row in audit_rows)
    events, _ = repos.exception_events.list_for_case(case.id)
    assert "EXECUTED" in {event.event_type for event in events}


def test_approve_is_idempotent_and_version_checked(db_session, bootstrap):
    repos = _support.repos_for(db_session, bootstrap)
    case = _support.detected_exception(repos, bootstrap)
    approval = _support.make_approval(
        repos,
        case,
        action="CREATE_FOLLOWUP",
        ai_payload={"title": "回访承运商", "content": "确认恢复", "priority": "NORMAL"},
    )

    with pytest.raises(AppError) as conflict:
        ApprovalExecutor(repos).approve(approval.id, actor_id=None, expected_version=approval.version + 5)
    assert conflict.value.code == ErrorCode.OPTIMISTIC_LOCK_CONFLICT

    result = ApprovalExecutor(repos).approve(approval.id, actor_id=None, expected_version=approval.version)
    assert result["status"] == str(ApprovalStatus.EXECUTED)
    assert result["execution_result"]["followup_task_id"]
    assert len(repos.followups.list_for_case(case.id)) == 1

    with pytest.raises(AppError) as decided:
        ApprovalExecutor(repos).approve(approval.id, actor_id=None, expected_version=None)
    assert decided.value.code == ErrorCode.APPROVAL_ALREADY_DECIDED


def test_reject_has_no_business_side_effect(db_session, bootstrap):
    repos = _support.repos_for(db_session, bootstrap)
    case = _support.detected_exception(repos, bootstrap)
    approval = _support.make_approval(
        repos, case, action="SAVE_NOTICE", ai_payload={"subject": "s", "content": "c"}
    )
    result = ApprovalExecutor(repos).reject(
        approval.id, actor_id=None, reason="通知语气不合适", expected_version=approval.version
    )
    assert result["status"] == str(ApprovalStatus.REJECTED)
    assert approval.reject_reason == "通知语气不合适"
    assert repos.notifications.list_for_case(case.id) == []
    events, _ = repos.exception_events.list_for_case(case.id)
    assert "REJECTED" in {event.event_type for event in events}


def test_save_notice_falls_back_to_template_when_ai_unavailable(db_session, bootstrap, monkeypatch):
    """AI 不可用：审批建议里的通知正文必须是确定性模板（含订单号），执行链路永远可用。"""
    repos = _support.repos_for(db_session, bootstrap)
    case = _support.detected_exception(repos, bootstrap)
    order_no = repos.orders.get(case.order_id).order_no
    monkeypatch.setattr(ai_bridge, "_load_runner", lambda: None)

    analysis = _support.make_analysis(
        repos,
        case,
        output=_support.ai_output(
            suggestions=[{"code": "SAVE_NOTICE", "title": "生成延误通知", "rationale": "VIP 需告知"}]
        ),
    )
    approval = build_approvals_from_analysis(repos, analysis)[0]
    assert approval.ai_payload_json["draft_origin"] == "TEMPLATE"
    assert order_no in approval.ai_payload_json["content"]

    result = ApprovalExecutor(repos).approve(approval.id, actor_id=None, expected_version=approval.version)
    assert result["status"] == str(ApprovalStatus.EXECUTED)
    notification = repos.notifications.list_for_case(case.id)[0]
    assert notification.status == str(NotificationStatus.DRAFT)
    assert order_no in notification.content
    assert notification.ai_draft_content


def test_approval_expires_after_24h_without_execution(db_session, bootstrap):
    repos = _support.repos_for(db_session, bootstrap)
    case = _support.detected_exception(repos, bootstrap)
    approval = _support.make_approval(repos, case, action="CREATE_FOLLOWUP", ai_payload={"title": "t"})

    clock_state.advance(24 * 60 + 1)
    expired = ApprovalExecutor(repos).expire_stale()
    assert expired == 1
    assert approval.status == str(ApprovalStatus.EXPIRED)
    assert repos.followups.list_for_case(case.id) == []


def test_batch_approve_reports_per_item_status(db_session, bootstrap):
    repos = _support.repos_for(db_session, bootstrap)
    case = _support.detected_exception(repos, bootstrap)
    analysis = _support.make_analysis(repos, case, output=_support.ai_output())
    approvals = build_approvals_from_analysis(repos, analysis)
    ids = [approval.id for approval in approvals]

    results = ApprovalExecutor(repos).batch_approve(
        exception_id=case.id,
        approval_ids=[*ids, 999999],
        actor_id=None,
        auto_execute=True,
    )
    statuses = [item["status"] for item in results]
    assert statuses[: len(ids)] == ["EXECUTED"] * len(ids), results[1]
    assert statuses[-1] == "NOT_FOUND"
    assert repos.notifications.list_for_case(case.id), "SAVE_NOTICE 批量批准后应有通知草稿"
    assert repos.followups.list_for_case(case.id), "CREATE_FOLLOWUP 批量批准后应有跟进任务"


def test_apply_analysis_result_uses_rules_for_level(db_session, bootstrap):
    """LLM 无权改 level：即使 output 里写 LOW，也按规则算成 CRITICAL。"""
    repos = _support.repos_for(db_session, bootstrap)
    case = _support.detected_exception(repos, bootstrap)
    ExceptionService(repos).confirm(case.id, expected_version=case.version, actor_id=None)
    apply_transition(case, "EXCEPTION", ExceptionStatus.ANALYZING)

    output = _support.ai_output()
    output["level"] = "LOW"
    output["risk_level"] = "LOW"
    analysis = _support.make_analysis(repos, case, output=output)
    result = ExceptionService(repos).apply_analysis_result(analysis.id)

    assert result["level"] == "CRITICAL"
    assert result["risk_score"] == 4
    assert analysis.risk_level_calculated == "CRITICAL"
    assert case.status == str(ExceptionStatus.PROCESSING)
    assert len(result["approval_ids"]) == 3
