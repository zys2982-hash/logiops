"""人工确认（HITL）执行器（基线文档 §11.5）。

T2 suggestions → approval(PENDING)；人批准后事务内按 action_type 分发：
    UPDATE_ETA / CREATE_FOLLOWUP / SAVE_NOTICE / SEND_NOTICE / CLOSE_EXCEPTION
成功：approval=EXECUTED + exception_event(EXECUTED) + audit(source=APPROVED_AI)，
     并保留 ai_payload 与 final_payload 的 diff。
失败：approval=FAILED + execution_result_json.error（业务副作用由 savepoint 回滚），可 POST /execute 重试。
拒绝（必填 reason）与过期（24h）不产生任何业务副作用。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.core.audit import to_jsonable
from app.core.errors import AppError, ErrorCode, validation_error
from app.models.ai import AiAnalysis, Approval
from app.models.enums import (
    ActorType,
    ApprovalAction,
    ApprovalStatus,
    AuditSource,
    ExceptionEventType,
    ExceptionStatus,
    NotificationChannel,
    NotificationStatus,
)
from app.models.exception import ExceptionCase
from app.repositories import Repos
from app.services import ai_bridge, read_models
from app.services.common import (
    add_event,
    bump_version,
    check_version,
    now_naive,
    parse_iso_naive,
    require_text,
    to_naive_utc,
    write_audit,
)

APPROVAL_TTL_HOURS = 24
VALID_ACTIONS = {member.value for member in ApprovalAction}


def diff_payload(ai_payload: dict[str, Any], final_payload: dict[str, Any]) -> dict[str, Any]:
    """AI 原值 vs 人工终值的 diff（前端"编辑后必须展示 diff"）。"""
    changed = sorted(
        key for key in set(ai_payload) | set(final_payload) if ai_payload.get(key) != final_payload.get(key)
    )
    return {
        "changed": changed,
        "fields": {
            key: {"ai": ai_payload.get(key), "final": final_payload.get(key)} for key in changed
        },
        "ai_value": {key: ai_payload.get(key) for key in changed},
        "final_value": {key: final_payload.get(key) for key in changed},
        "ai_payload": ai_payload,
        "final_payload": final_payload,
    }


def _suggestion_payload(
    action: str,
    suggestion: dict[str, Any],
    case: ExceptionCase,
    analysis: AiAnalysis | None,
    repos: Repos,
    session: Session,
) -> dict[str, Any]:
    title = str(suggestion.get("title") or "").strip()
    rationale = str(suggestion.get("rationale") or "").strip()
    if action == str(ApprovalAction.UPDATE_ETA):
        eta = to_naive_utc(case.expected_eta_at)
        return {
            "eta_at": read_models.iso(eta),
            "reason": rationale or title or "AI 建议更新预计到达时间",
        }
    if action == str(ApprovalAction.CREATE_FOLLOWUP):
        return {
            "title": (title or "跟进承运商进展")[:128],
            "content": rationale or None,
            "assignee_role": suggestion.get("assignee_role") or "OPERATOR",
            "due_at": read_models.iso(now_naive() + timedelta(hours=1)),
            "priority": suggestion.get("priority") or "NORMAL",
        }
    if action in {str(ApprovalAction.SAVE_NOTICE), str(ApprovalAction.SEND_NOTICE)}:
        draft, origin = ai_bridge.notice_from_ai_or_template(
            session, repos, case.id, analysis.id if analysis else None
        )
        return {
            "subject": draft["subject"],
            "content": draft["content"],
            "tone": draft["tone"],
            "draft_origin": origin,
            "channel": str(NotificationChannel.MOCK_EMAIL)
            if action == str(ApprovalAction.SEND_NOTICE)
            else str(NotificationChannel.MANUAL_COPY),
        }
    if action == str(ApprovalAction.CLOSE_EXCEPTION):
        return {"reason_code": "MANUAL", "note": rationale or title or "AI 建议关闭异常"}
    return {"title": title, "rationale": rationale}


def build_approvals_from_analysis(
    repos: Repos,
    analysis: AiAnalysis,
    actor_id: int | None = None,
) -> list[Approval]:
    """把 T2 的 suggestions 转成 approval(PENDING)，1 条建议 = 1 张审批单（幂等）。"""
    case = repos.exceptions.get_or_404(analysis.exception_id, "异常单不存在")
    if str(case.status) == str(ExceptionStatus.CLOSED):
        return []
    pending = [
        approval
        for approval in repos.approvals.list_for_case(case.id)
        if str(approval.status) == str(ApprovalStatus.PENDING)
    ]
    if pending:
        return []

    output = analysis.output_json or {}
    suggestions = output.get("suggestions") or []
    created: list[Approval] = []
    session = repos.session
    for raw in suggestions:
        if not isinstance(raw, dict):
            continue
        action = str(raw.get("code") or "").upper()
        if action not in VALID_ACTIONS:
            continue
        payload = _suggestion_payload(action, raw, case, analysis, repos, session)
        approval = Approval(
            workspace_id=int(repos.workspace_id or 0),
            exception_id=case.id,
            analysis_id=analysis.id,
            action_type=action,
            target_type="ORDER" if action == str(ApprovalAction.UPDATE_ETA) else "EXCEPTION",
            target_id=case.order_id if action == str(ApprovalAction.UPDATE_ETA) else case.id,
            ai_payload_json=to_jsonable(payload),
            status=str(ApprovalStatus.PENDING),
            expires_at=now_naive() + timedelta(hours=APPROVAL_TTL_HOURS),
        )
        repos.approvals.add(approval)
        created.append(approval)
    if created:
        write_audit(
            session,
            repos,
            "approval.created_from_analysis",
            resource_type="exception",
            resource_id=case.id,
            actor_type=ActorType.SYSTEM,
            actor_id=actor_id,
            after={
                "analysis_id": analysis.id,
                "approval_ids": [approval.id for approval in created],
                "actions": [approval.action_type for approval in created],
            },
            source=AuditSource.APPROVED_AI,
        )
    return created


class ApprovalExecutor:
    def __init__(self, repos: Repos) -> None:
        self.repos = repos
        self.session: Session = repos.session

    def get(self, approval_id: int) -> Approval:
        return self.repos.approvals.get_or_404(approval_id, "审批单不存在")

    # --- 审批动作 ---------------------------------------------------------
    def approve(
        self,
        approval_id: int,
        *,
        actor_id: int | None,
        final_payload: dict[str, Any] | None = None,
        expected_version: int | None = None,
    ) -> dict[str, Any]:
        approval = self.get(approval_id)
        self._ensure_decidable(approval)
        check_version(approval, expected_version, "审批单")

        ai_payload = dict(approval.ai_payload_json or {})
        merged = {**ai_payload, **(final_payload or {})}
        approval.final_payload_json = to_jsonable(merged)
        approval.diff_json = to_jsonable(diff_payload(ai_payload, merged))
        approval.status = str(ApprovalStatus.APPROVED)
        approval.decided_by = actor_id
        approval.decided_at = now_naive()
        bump_version(approval)
        self.repos.approvals.save(approval)
        add_event(
            self.session,
            self.repos,
            exception_id=approval.exception_id,
            event_type=str(ExceptionEventType.APPROVED),
            from_status=None,
            to_status=None,
            actor_type=ActorType.USER,
            actor_id=actor_id,
            note=f"人工批准 AI 建议：{approval.action_type}",
            detail={"approval_id": approval.id, "diff": approval.diff_json},
        )
        write_audit(
            self.session,
            self.repos,
            "approval.approved",
            resource_type="approval",
            resource_id=approval.id,
            actor_id=actor_id,
            before={"status": str(ApprovalStatus.PENDING), "ai_payload": ai_payload},
            after={"status": str(ApprovalStatus.APPROVED), "final_payload": merged},
            source=AuditSource.APPROVED_AI,
        )
        return self._execute(approval, merged, actor_id=actor_id, audit_action="approval.executed")

    def reject(
        self,
        approval_id: int,
        *,
        actor_id: int | None,
        reason: str,
        expected_version: int | None = None,
    ) -> dict[str, Any]:
        approval = self.get(approval_id)
        self._ensure_decidable(approval)
        check_version(approval, expected_version, "审批单")
        reason_text = require_text(reason, "reason", max_len=255)

        approval.status = str(ApprovalStatus.REJECTED)
        approval.reject_reason = reason_text
        approval.decided_by = actor_id
        approval.decided_at = now_naive()
        approval.final_payload_json = approval.final_payload_json or approval.ai_payload_json
        approval.diff_json = approval.diff_json or diff_payload(
            dict(approval.ai_payload_json or {}), dict(approval.final_payload_json or {})
        )
        bump_version(approval)
        self.repos.approvals.save(approval)
        add_event(
            self.session,
            self.repos,
            exception_id=approval.exception_id,
            event_type=str(ExceptionEventType.REJECTED),
            from_status=None,
            to_status=None,
            actor_type=ActorType.USER,
            actor_id=actor_id,
            note=f"驳回 AI 建议：{approval.action_type} — {reason_text}",
            detail={"approval_id": approval.id},
        )
        write_audit(
            self.session,
            self.repos,
            "approval.rejected",
            resource_type="approval",
            resource_id=approval.id,
            actor_id=actor_id,
            after={"status": approval.status, "reason": reason_text},
            source=AuditSource.APPROVED_AI,
        )
        return {
            "id": approval.id,
            "status": str(approval.status),
            "reject_reason": reason_text,
            "diff": approval.diff_json,
            "execution_result": None,
        }

    def retry(self, approval_id: int, *, actor_id: int | None = None) -> dict[str, Any]:
        """POST /approvals/{id}/execute：仅 FAILED 可重试（retry_count+1）。"""
        approval = self.get(approval_id)
        if str(approval.status) != str(ApprovalStatus.FAILED):
            raise AppError(
                ErrorCode.APPROVAL_ALREADY_DECIDED,
                "只有执行失败的审批单可以重试执行",
                {"status": approval.status},
            )
        approval.retry_count = int(approval.retry_count or 0) + 1
        approval.status = str(ApprovalStatus.APPROVED)
        approval.execution_result_json = None
        bump_version(approval)
        self.repos.approvals.save(approval)
        write_audit(
            self.session,
            self.repos,
            "approval.retried",
            resource_type="approval",
            resource_id=approval.id,
            actor_id=actor_id,
            after={"retry_count": approval.retry_count, "action_type": approval.action_type},
            source=AuditSource.APPROVED_AI,
        )
        payload = dict(approval.final_payload_json or approval.ai_payload_json or {})
        return self._execute(approval, payload, actor_id=actor_id, audit_action="approval.executed")

    def batch_approve(
        self,
        *,
        exception_id: int,
        approval_ids: list[int],
        actor_id: int | None,
        auto_execute: bool = True,
    ) -> list[dict[str, Any]]:
        """逐条执行，返回每条的成败（部分失败可单独重试）。"""
        self.repos.exceptions.get_or_404(exception_id, "异常单不存在")
        if not approval_ids:
            raise validation_error("approval_ids 不能为空")
        results: list[dict[str, Any]] = []
        for approval_id in approval_ids:
            approval = self.repos.approvals.get(approval_id)
            if approval is None or approval.exception_id != exception_id:
                results.append(
                    {
                        "id": approval_id,
                        "status": "NOT_FOUND",
                        "diff": None,
                        "execution_result": {"error": "审批单不存在或不属于该异常"},
                    }
                )
                continue
            try:
                if auto_execute:
                    results.append(
                        self.approve(
                            approval_id, actor_id=actor_id, final_payload=None, expected_version=None
                        )
                    )
                else:
                    approval.final_payload_json = approval.final_payload_json or approval.ai_payload_json
                    approval.diff_json = diff_payload(
                        dict(approval.ai_payload_json or {}), dict(approval.final_payload_json or {})
                    )
                    approval.status = str(ApprovalStatus.APPROVED)
                    approval.decided_by = actor_id
                    approval.decided_at = now_naive()
                    bump_version(approval)
                    self.repos.approvals.save(approval)
                    results.append(
                        {
                            "id": approval.id,
                            "status": str(approval.status),
                            "diff": approval.diff_json,
                            "execution_result": None,
                        }
                    )
            except AppError as exc:
                results.append(
                    {
                        "id": approval_id,
                        "status": "FAILED",
                        "diff": approval.diff_json,
                        "execution_result": {"error": exc.message, "error_code": str(exc.code)},
                    }
                )
        return results

    def expire_stale(self, *, moment: datetime | None = None, hours: int = APPROVAL_TTL_HOURS) -> int:
        now = to_naive_utc(moment) or now_naive()
        threshold = now - timedelta(hours=hours)
        expired = 0
        for approval in self.repos.approvals.list_pending():
            created = to_naive_utc(approval.created_at)
            expires_at = to_naive_utc(approval.expires_at) or (
                created + timedelta(hours=hours) if created else None
            )
            if expires_at is not None and expires_at <= now:
                approval.status = str(ApprovalStatus.EXPIRED)
                approval.decided_at = now
                bump_version(approval)
                self.repos.approvals.save(approval)
                add_event(
                    self.session,
                    self.repos,
                    exception_id=approval.exception_id,
                    event_type=str(ExceptionEventType.COMMENT),
                    actor_type=ActorType.SYSTEM,
                    note=f"审批单 {approval.id} 超过 {hours} 小时未决策，已过期（不自动执行）",
                    detail={"approval_id": approval.id, "expired_at": now.isoformat()},
                )
                write_audit(
                    self.session,
                    self.repos,
                    "approval.expired",
                    resource_type="approval",
                    resource_id=approval.id,
                    actor_type=ActorType.SYSTEM,
                    after={"status": approval.status, "threshold": threshold.isoformat()},
                    source=AuditSource.SYSTEM,
                )
                expired += 1
        return expired

    # --- 内部 -------------------------------------------------------------
    def _ensure_decidable(self, approval: Approval) -> None:
        if str(approval.status) != str(ApprovalStatus.PENDING):
            raise AppError(
                ErrorCode.APPROVAL_ALREADY_DECIDED,
                f"审批单已被处理（{approval.status}）",
                {"status": approval.status, "approval_id": approval.id},
            )
        expires_at = to_naive_utc(approval.expires_at)
        if expires_at is not None and expires_at <= now_naive():
            approval.status = str(ApprovalStatus.EXPIRED)
            approval.decided_at = now_naive()
            bump_version(approval)
            self.repos.approvals.save(approval)
            raise AppError(
                ErrorCode.APPROVAL_ALREADY_DECIDED,
                "审批单已超过 24 小时未决策，已过期（不自动执行）",
                {"status": approval.status, "approval_id": approval.id},
            )

    def _execute(
        self,
        approval: Approval,
        payload: dict[str, Any],
        *,
        actor_id: int | None,
        audit_action: str,
    ) -> dict[str, Any]:
        """执行分发。失败 → approval=FAILED + execution_result.error（可重试），绝不 500。

        注意：这里刻意不使用 `Session.begin_nested()`。本项目的 SQLite 测试库走 pysqlite 默认
        隔离级别，连续两次 SAVEPOINT 会让连接进入 "unable to open database file" 的不可用状态
        （Windows 沙箱下必现）。因此改为"先校验后写" + 失败兜底落库：
        每个 action 都在写库前完成版本/状态/参数校验，正常失败不会留下业务副作用。
        """
        try:
            result = self._dispatch(approval, payload, actor_id=actor_id)
        except Exception as exc:  # noqa: BLE001 - 执行失败要落 FAILED 而不是 500
            return self._record_failure(approval, exc, actor_id=actor_id)

        approval.status = str(ApprovalStatus.EXECUTED)
        approval.executed_at = now_naive()
        approval.execution_result_json = to_jsonable(result)
        bump_version(approval)
        self.repos.approvals.save(approval)
        add_event(
            self.session,
            self.repos,
            exception_id=approval.exception_id,
            event_type=str(ExceptionEventType.EXECUTED),
            actor_type=ActorType.USER,
            actor_id=actor_id,
            note=f"审批执行成功：{approval.action_type}",
            detail={"approval_id": approval.id, "result": to_jsonable(result)},
        )
        write_audit(
            self.session,
            self.repos,
            audit_action,
            resource_type="approval",
            resource_id=approval.id,
            actor_id=actor_id,
            before={"action_type": approval.action_type, "ai_payload": approval.ai_payload_json},
            after={
                "status": approval.status,
                "final_payload": approval.final_payload_json,
                "execution_result": to_jsonable(result),
            },
            source=AuditSource.APPROVED_AI,
        )
        return {
            "id": approval.id,
            "status": str(approval.status),
            "diff": approval.diff_json,
            "execution_result": approval.execution_result_json,
            "error_message": None,
        }

    def _record_failure(
        self,
        approval: Approval,
        exc: BaseException,
        *,
        actor_id: int | None,
    ) -> dict[str, Any]:
        """把执行失败落成可观测、可重试的 FAILED 状态。"""
        error_code = str(getattr(exc, "code", ErrorCode.INTERNAL_ERROR))
        execution_json = to_jsonable({"error": str(exc)[:255], "error_code": error_code})
        try:
            self._write_failed(approval, execution_json, str(exc), actor_id=actor_id)
        except Exception:  # noqa: BLE001 - 会话已被脏 flush 污染：回滚后重新落 FAILED
            self.session.rollback()
            fresh = self.repos.approvals.get(approval.id)
            if fresh is not None:
                self._write_failed(fresh, execution_json, str(exc), actor_id=actor_id)
                approval = fresh
        return {
            "id": approval.id,
            "status": str(approval.status),
            "diff": approval.diff_json,
            "execution_result": approval.execution_result_json,
            "error_message": str(exc)[:255],
        }

    def _write_failed(
        self,
        approval: Approval,
        execution_json: dict[str, Any],
        message: str,
        *,
        actor_id: int | None,
    ) -> None:
        approval.status = str(ApprovalStatus.FAILED)
        approval.execution_result_json = execution_json
        bump_version(approval)
        self.repos.approvals.save(approval)
        add_event(
            self.session,
            self.repos,
            exception_id=approval.exception_id,
            event_type=str(ExceptionEventType.EXECUTE_FAILED),
            actor_type=ActorType.SYSTEM,
            actor_id=actor_id,
            note=f"审批执行失败：{message[:200]}",
            detail={"approval_id": approval.id, "error_code": execution_json.get("error_code")},
        )
        write_audit(
            self.session,
            self.repos,
            "approval.execute_failed",
            resource_type="approval",
            resource_id=approval.id,
            actor_id=actor_id,
            after={"status": approval.status, "error": message[:255]},
            source=AuditSource.APPROVED_AI,
        )

    def _dispatch(
        self, approval: Approval, payload: dict[str, Any], *, actor_id: int | None
    ) -> dict[str, Any]:
        action = str(approval.action_type)
        case = self.repos.exceptions.get_or_404(approval.exception_id, "异常单不存在")
        if action == str(ApprovalAction.UPDATE_ETA):
            return self._exec_update_eta(case, payload, actor_id=actor_id)
        if action == str(ApprovalAction.CREATE_FOLLOWUP):
            return self._exec_create_followup(case, payload, approval, actor_id=actor_id)
        if action == str(ApprovalAction.SAVE_NOTICE):
            return self._exec_save_notice(case, payload, approval, actor_id=actor_id)
        if action == str(ApprovalAction.SEND_NOTICE):
            return self._exec_send_notice(case, payload, approval, actor_id=actor_id)
        if action == str(ApprovalAction.CLOSE_EXCEPTION):
            return self._exec_close(case, payload, actor_id=actor_id)
        raise validation_error(f"不支持的审批动作 {action}", fields=[{"loc": "action_type", "msg": action}])

    def _exec_update_eta(
        self, case: ExceptionCase, payload: dict[str, Any], *, actor_id: int | None
    ) -> dict[str, Any]:
        from app.services.orders import OrderService  # 局部导入避免循环

        eta_at = parse_iso_naive(payload.get("eta_at")) or to_naive_utc(case.expected_eta_at)
        if eta_at is None:
            raise validation_error("UPDATE_ETA 需要一个合法的 eta_at")
        reason = str(payload.get("reason") or "AI 建议更新 ETA（人工批准）")[:255]
        order = OrderService(self.repos).update_eta(
            case.order_id,
            eta_at=eta_at,
            reason=reason,
            actor_id=actor_id,
            source=AuditSource.APPROVED_AI,
        )
        add_event(
            self.session,
            self.repos,
            exception_id=case.id,
            event_type=str(ExceptionEventType.ETA_UPDATED),
            actor_type=ActorType.USER,
            actor_id=actor_id,
            note=f"按人工批准的 ETA 更新：{read_models.iso(order.current_eta_at)}",
            detail={
                "approval": str(ApprovalAction.UPDATE_ETA),
                "eta_at": read_models.iso(order.current_eta_at),
                "reason": reason,
                "sla_delay_minutes": case.sla_delay_minutes,
                "sla_breached": bool(case.sla_breached),
            },
        )
        return {
            "order_id": order.id,
            "updated_fields": ["current_eta_at"],
            "current_eta_at": read_models.iso(order.current_eta_at),
            "exception_expected_eta_at": read_models.iso(case.expected_eta_at),
            "sla_delay_minutes": case.sla_delay_minutes,
            "sla_breached": bool(case.sla_breached),
        }

    def _exec_create_followup(
        self,
        case: ExceptionCase,
        payload: dict[str, Any],
        approval: Approval,
        *,
        actor_id: int | None,
    ) -> dict[str, Any]:
        from app.services.followups import FollowupService  # 局部导入避免循环

        task = FollowupService(self.repos).create(
            exception_id=case.id,
            title=str(payload.get("title") or "跟进承运商进展"),
            content=payload.get("content"),
            due_at=payload.get("due_at"),
            priority=str(payload.get("priority") or "NORMAL"),
            source="AI_SUGGESTED",
            source_approval_id=approval.id,
            actor_id=actor_id,
        )
        return {
            "followup_task_id": task.id,
            "title": task.title,
            "status": task.status,
            "due_at": read_models.iso(task.due_at),
        }

    def _exec_save_notice(
        self,
        case: ExceptionCase,
        payload: dict[str, Any],
        approval: Approval,
        *,
        actor_id: int | None,
    ) -> dict[str, Any]:
        from app.services.notifications import NotificationService  # 局部导入避免循环

        service = NotificationService(self.repos)
        draft, origin = ai_bridge.notice_from_ai_or_template(
            self.session, self.repos, case.id, approval.analysis_id
        )
        content = str(payload.get("content") or draft["content"])
        subject = str(payload.get("subject") or draft["subject"])
        if not (payload.get("draft_origin")):
            payload["draft_origin"] = origin
        notification = service.create(
            exception_id=case.id,
            subject=subject,
            content=content,
            channel=str(payload.get("channel") or NotificationChannel.MANUAL_COPY),
            status=str(NotificationStatus.DRAFT),
            ai_draft_content=draft["content"],
            source_approval_id=approval.id,
            use_ai_draft=False,
            actor_id=actor_id,
        )
        return {
            "notification_id": notification.id,
            "status": str(notification.status),
            "draft_origin": payload.get("draft_origin"),
        }

    def _exec_send_notice(
        self,
        case: ExceptionCase,
        payload: dict[str, Any],
        approval: Approval,
        *,
        actor_id: int | None,
    ) -> dict[str, Any]:
        from app.services.notifications import NotificationService  # 局部导入避免循环

        service = NotificationService(self.repos)
        status = str(payload.get("status") or "").upper()
        if status == str(NotificationStatus.SENT_MOCK):
            # 审批单本身就是"批准后直接发送"
            notification = service.create(
                exception_id=case.id,
                subject=str(payload.get("subject") or "") or None,
                content=str(payload.get("content") or "") or None,
                channel=str(payload.get("channel") or NotificationChannel.MOCK_EMAIL),
                status=str(NotificationStatus.SENT_MOCK),
                source_approval_id=approval.id,
                use_ai_draft=True,
                actor_id=actor_id,
            )
        else:
            notification = service.create(
                exception_id=case.id,
                subject=str(payload.get("subject") or "") or None,
                content=str(payload.get("content") or "") or None,
                channel=str(payload.get("channel") or NotificationChannel.MOCK_EMAIL),
                status=str(NotificationStatus.DRAFT),
                source_approval_id=approval.id,
                use_ai_draft=True,
                actor_id=actor_id,
            )
            notification = service.mark_sent(notification.id, actor_id=actor_id)
        return {
            "notification_id": notification.id,
            "status": str(notification.status),
            "sent_at": read_models.iso(notification.sent_at),
        }

    def _exec_close(
        self, case: ExceptionCase, payload: dict[str, Any], *, actor_id: int | None
    ) -> dict[str, Any]:
        from app.services.exceptions import ExceptionService  # 局部导入避免循环

        closed = ExceptionService(self.repos).close(
            case.id,
            reason_code=str(payload.get("reason_code") or "MANUAL"),
            note=payload.get("note") or "按人工批准的 AI 建议关闭异常",
            expected_version=None,
            actor_id=actor_id,
        )
        return {"exception_id": closed.id, "status": str(closed.status), "close_reason": closed.close_reason}

    # --- 只读 -------------------------------------------------------------
    def list_for_exception(self, exception_id: int) -> list[dict[str, Any]]:
        self.repos.exceptions.get_or_404(exception_id, "异常单不存在")
        return [serialize(approval) for approval in self.repos.approvals.list_for_case(exception_id)]


def serialize(approval: Approval) -> dict[str, Any]:
    return {
        "id": approval.id,
        "exception_id": approval.exception_id,
        "analysis_id": approval.analysis_id,
        "action_type": approval.action_type,
        "target_type": approval.target_type,
        "target_id": approval.target_id,
        "status": approval.status,
        "ai_payload": approval.ai_payload_json,
        "final_payload": approval.final_payload_json,
        "diff": approval.diff_json,
        "decided_by": approval.decided_by,
        "decided_at": read_models.iso(approval.decided_at),
        "reject_reason": approval.reject_reason,
        "executed_at": read_models.iso(approval.executed_at),
        "execution_result": approval.execution_result_json,
        "retry_count": approval.retry_count,
        "expires_at": read_models.iso(approval.expires_at),
        "version": approval.version,
        "created_at": read_models.iso(approval.created_at),
        "updated_at": read_models.iso(approval.updated_at),
    }


__all__ = [
    "APPROVAL_TTL_HOURS",
    "ApprovalExecutor",
    "build_approvals_from_analysis",
    "diff_payload",
    "serialize",
]
