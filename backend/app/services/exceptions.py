"""异常服务：手工建单 / 确认 / 触发 AI 分析 / 承运商消息 / 解决 / 关闭 / 详情 / 时间线。

状态机全部走 common.apply_transition（内部 plan_transition），非法流转 409 STATE_TRANSITION_INVALID。
AI 调用一律经 ai_bridge：AI 不可用时 ai_analysis=FAILED + LLM_UNAVAILABLE，异常状态 ANALYZING → CONFIRMING，
接口不允许 500（基线文档 §8.2 / §11.8）。
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError, ErrorCode, validation_error
from app.models.ai import AiAnalysis
from app.models.enums import (
    ActorType,
    AnalysisStatus,
    AnalysisTaskType,
    ApprovalStatus,
    AuditSource,
    DetectionRule,
    ExceptionEventType,
    ExceptionLevel,
    ExceptionStatus,
    MessageChannel,
    ParseStatus,
    RootCauseCode,
    VehicleStatus,
)
from app.models.exception import CarrierMessage, ExceptionCase
from app.models.transport import Order
from app.repositories import Repos
from app.rules import state_machine
from app.services import ai_bridge, detection_flow, eta_flow, read_models
from app.services.common import (
    add_event,
    apply_transition,
    bump_version,
    check_version,
    next_analysis_no,
    now_naive,
    parse_iso_naive,
    require_text,
    to_naive_utc,
    write_audit,
)

EXCEPTION_KIND = state_machine.EntityKind.EXCEPTION
REUSE_WINDOW_MINUTES = 15
ANALYSIS_TIMEOUT_SECONDS = 90
VALID_CLOSE_REASONS = {"INVALID", "DELIVERED", "MANUAL", "ORDER_CANCELLED", "FORCED_CLOSE"}
# "预计晚上 8 点恢复" / "20:30 恢复" 这类相对时间（确定性兜底解析）
TIME_HINT_RE = re.compile(
    r"(?:(凌晨|早上|上午|中午|下午|傍晚|晚上)\s*)?(\d{1,2})\s*(?:[:：]\s*(\d{2})|点\s*(\d{0,2})?)"
)
PARSE_FIELDS = (
    "exception_type",
    "location",
    "status",
    "estimated_recovery_at",
    "confidence",
    "missing_info",
)


def search_exceptions(
    repos: Repos,
    *,
    status: str | None = None,
    level: str | None = None,
    type_: str | None = None,
    customer_id: int | None = None,
    sla_breached: bool | None = None,
    assigned_to: int | None = None,
    keyword: str | None = None,
    order_by: list[Any] | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[ExceptionCase], int]:
    """异常列表查询（在仓储 search 之上补一层：关键字也能命中订单号）。"""
    filters: list[Any] = []
    if status:
        filters.append(ExceptionCase.status == status)
    if level:
        filters.append(ExceptionCase.level == level)
    if type_:
        filters.append(ExceptionCase.type == type_)
    if customer_id:
        filters.append(ExceptionCase.customer_id == customer_id)
    if sla_breached is not None:
        filters.append(ExceptionCase.sla_breached.is_(sla_breached))
    if assigned_to:
        filters.append(ExceptionCase.assigned_to == assigned_to)
    if keyword:
        like = f"%{keyword}%"
        order_ids = select(Order.id).where(Order.order_no.like(like))
        if repos.workspace_id is not None:
            order_ids = order_ids.where(Order.workspace_id == repos.workspace_id)
        filters.append(
            or_(
                ExceptionCase.case_no.like(like),
                ExceptionCase.impact_summary.like(like),
                ExceptionCase.root_cause_note.like(like),
                ExceptionCase.order_id.in_(order_ids),
            )
        )
    ordering = order_by or [ExceptionCase.risk_score.desc(), ExceptionCase.id.desc()]
    return repos.exceptions.list(filters=filters, order_by=ordering, page=page, page_size=page_size)


class ExceptionService:
    def __init__(self, repos: Repos) -> None:
        self.repos = repos
        self.session: Session = repos.session

    # --- 基础设施 ---------------------------------------------------------
    def get(self, exception_id: int) -> ExceptionCase:
        return self.repos.exceptions.get_or_404(exception_id, "异常单不存在")

    def input_hash(self, case: ExceptionCase) -> str:
        """T2 幂等键：异常关键字段 + 最新轨迹 id + 最新消息 id（§11.2 表 1）。"""
        latest_tracking = self.repos.tracking.latest(case.order_id)
        latest_message = self.repos.messages.latest_for_case(case.id)
        order = self.repos.orders.get(case.order_id)
        parts = [
            str(case.id),
            str(case.case_no),
            str(case.type),
            str(case.status),
            str(case.order_id),
            str(order.status) if order else "-",
            str(order.current_eta_at) if order else "-",
            str(case.expected_eta_at),
            str(case.sla_delay_minutes),
            str(latest_tracking.id if latest_tracking else 0),
            str(latest_message.id if latest_message else 0),
            str(latest_message.raw_text if latest_message else ""),
        ]
        return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()

    def _reuse_candidate(self, case: ExceptionCase, input_hash: str) -> AiAnalysis | None:
        """最新 READY 且 input_hash 未变且 finished_at 在 15 分钟内 → 可复用。"""
        latest = self.repos.analyses.latest_for_case(case.id)
        if latest is None or str(latest.status) != str(AnalysisStatus.READY):
            return None
        if latest.input_hash not in (None, input_hash):
            return None
        finished = to_naive_utc(latest.finished_at) or to_naive_utc(latest.created_at)
        if finished is None:
            return None
        if now_naive() - finished > timedelta(minutes=REUSE_WINDOW_MINUTES):
            return None
        return latest

    # --- 手工建单 ---------------------------------------------------------
    def create_manual(
        self,
        *,
        order_id: int,
        type: str,
        occurred_at: datetime | str,
        note: str,
        level: str | None = None,
        actor_id: int | None = None,
    ) -> ExceptionCase:
        order = self.repos.orders.get_or_404(order_id, "订单不存在")
        kind = detection_flow.validate_exception_type(type)
        occurred = parse_iso_naive(occurred_at)
        if occurred is None:
            raise validation_error("occurred_at 必填且格式合法")
        note_text = require_text(note, "note", max_len=500)
        if level is not None:
            text = str(level).upper()
            if text not in {member.value for member in ExceptionLevel}:
                raise validation_error("level 非法", fields=[{"loc": "level", "msg": text}])

        existing = self.repos.exceptions.find_open_by_order(order.id)
        if existing is not None:
            raise AppError(
                ErrorCode.OPEN_EXCEPTION_EXISTS,
                "该订单已有未关闭异常",
                {"exception_id": existing.id, "case_no": existing.case_no, "status": existing.status},
            )

        case = detection_flow.create_case_record(
            self.repos,
            order,
            exception_type=kind,
            occurred_at=occurred,
            detection_rule=str(DetectionRule.MANUAL),
            detected_by="OPERATOR",
            moment=now_naive(),
            actor_id=actor_id,
        )
        case.impact_summary = note_text
        if level is not None:
            case.level = str(level).upper()
        bump_version(case)
        self.repos.exceptions.save(case)

        add_event(
            self.session,
            self.repos,
            exception_id=case.id,
            event_type=str(ExceptionEventType.DETECTED),
            from_status=None,
            to_status=case.status,
            actor_type=ActorType.USER,
            actor_id=actor_id,
            note=note_text,
            detail={"rule": str(DetectionRule.MANUAL), "type": kind, "manual": True},
        )
        write_audit(
            self.session,
            self.repos,
            "exception.created_manual",
            resource_type="exception",
            resource_id=case.id,
            actor_id=actor_id,
            after={
                "case_no": case.case_no,
                "order_id": order.id,
                "type": kind,
                "level": case.level,
                "risk_score": case.risk_score,
            },
        )
        return case

    # --- 修改（不改 status） ---------------------------------------------
    def update_basic(
        self,
        exception_id: int,
        *,
        expected_version: int | None = None,
        actor_id: int | None = None,
        assigned_to: int | None = None,
        remark: str | None = None,
    ) -> ExceptionCase:
        """PATCH /exceptions/{id}：只改 assigned_to / remark，不改 status（§10.3）。"""
        case = self.get(exception_id)
        check_version(case, expected_version, "异常单")
        if state_machine.is_terminal(EXCEPTION_KIND, case.status):
            raise AppError(
                ErrorCode.STATE_TRANSITION_INVALID,
                "异常单已关闭，不可修改",
                {"from": case.status},
            )
        before = {"assigned_to": case.assigned_to, "impact_summary": case.impact_summary}
        if assigned_to is not None:
            self.repos.users.get_or_404(assigned_to, "用户不存在")
            case.assigned_to = assigned_to
        if remark is not None:
            case.impact_summary = remark[:255] or case.impact_summary
        bump_version(case)
        self.repos.exceptions.save(case)
        add_event(
            self.session,
            self.repos,
            exception_id=case.id,
            event_type=str(ExceptionEventType.COMMENT),
            from_status=case.status,
            to_status=case.status,
            actor_type=ActorType.USER,
            actor_id=actor_id,
            note="更新处理人/备注",
            detail={"assigned_to": case.assigned_to, "remark": remark},
        )
        write_audit(
            self.session,
            self.repos,
            "exception.updated",
            resource_type="exception",
            resource_id=case.id,
            actor_id=actor_id,
            before=before,
            after={"assigned_to": case.assigned_to, "impact_summary": case.impact_summary},
        )
        return case

    # --- 确认 -------------------------------------------------------------
    def confirm(
        self,
        exception_id: int,
        *,
        expected_version: int | None = None,
        actor_id: int | None = None,
        note: str | None = None,
    ) -> ExceptionCase:
        case = self.get(exception_id)
        check_version(case, expected_version, "异常单")
        plan = apply_transition(case, EXCEPTION_KIND, ExceptionStatus.CONFIRMING)
        bump_version(case)
        self.repos.exceptions.save(case)
        add_event(
            self.session,
            self.repos,
            exception_id=case.id,
            event_type=str(ExceptionEventType.CONFIRMED),
            from_status=plan.from_status,
            to_status=plan.to_status,
            actor_type=ActorType.USER,
            actor_id=actor_id,
            note=note or "人工确认信息完整，进入分析准备",
        )
        write_audit(
            self.session,
            self.repos,
            "exception.confirmed",
            resource_type="exception",
            resource_id=case.id,
            actor_id=actor_id,
            before={"status": plan.from_status},
            after={"status": plan.to_status},
        )
        return case

    # --- AI 分析 ----------------------------------------------------------
    def request_analysis(
        self,
        exception_id: int,
        *,
        expected_version: int | None = None,
        actor_id: int | None = None,
    ) -> dict[str, Any]:
        case = self.get(exception_id)
        check_version(case, expected_version, "异常单")
        digest = self.input_hash(case)

        reused = self._reuse_candidate(case, digest)
        if reused is not None:
            analysis = self._clone_reuse(case, reused, actor_id=actor_id)
            add_event(
                self.session,
                self.repos,
                exception_id=case.id,
                event_type=str(ExceptionEventType.ANALYSIS_REQUESTED),
                from_status=case.status,
                to_status=case.status,
                actor_type=ActorType.USER,
                actor_id=actor_id,
                note="输入未变化，复用 15 分钟内的分析结果",
                detail={"reused_from_id": reused.id, "analysis_id": analysis.id},
            )
            write_audit(
                self.session,
                self.repos,
                "exception.analysis_reused",
                resource_type="exception",
                resource_id=case.id,
                actor_id=actor_id,
                after={"analysis_id": analysis.id, "reused_from_id": reused.id},
            )
            return {
                "analysis_id": analysis.id,
                "status": str(analysis.status),
                "reused": True,
                "reused_from_id": reused.id,
                "exception_status": case.status,
                "input_hash": digest,
                "error_code": None,
            }

        running = self.repos.analyses.find_running(case.id)
        if running is not None:
            raise AppError(
                ErrorCode.AI_ANALYSIS_IN_PROGRESS,                "该异常已有分析任务在执行",
                {"analysis_id": running.id, "status": running.status},
            )

        # 前置给出可执行的提示（比裸的状态机报错"不允许的状态流转"更容易理解）
        if str(case.status) != str(ExceptionStatus.CONFIRMING):
            hint = {
                "DETECTED": "请先点「确认异常」，再发起 AI 分析",
                "PROCESSING": "该异常已进入处理中：分析结论已产出，请直接处理建议（如需重新分析请先重置演示数据）",
                "RESOLVED": "该异常已解决，无需再分析",
                "CLOSED": "该异常已关闭，不能再分析",
            }.get(str(case.status), "只有状态为「确认中」的异常可以发起 AI 分析")
            raise AppError(
                ErrorCode.STATE_TRANSITION_INVALID,
                f"当前状态（{case.status}）不能发起 AI 分析：{hint}",
                {"from": str(case.status), "to": str(ExceptionStatus.ANALYZING)},
            )

        plan = apply_transition(case, EXCEPTION_KIND, ExceptionStatus.ANALYZING)
        bump_version(case)
        self.repos.exceptions.save(case)
        analysis = AiAnalysis(
            workspace_id=int(self.repos.workspace_id or 0),
            exception_id=case.id,
            analysis_no=next_analysis_no(self.repos),
            task_type=str(AnalysisTaskType.ANALYZE_EXCEPTION),
            status=str(AnalysisStatus.RUNNING),
            triggered_by=actor_id,
            input_hash=digest,
            model=get_settings().llm_model,
            prompt_version="v1",
            started_at=now_naive(),
        )
        self.repos.analyses.add(analysis)
        add_event(
            self.session,
            self.repos,
            exception_id=case.id,
            event_type=str(ExceptionEventType.ANALYSIS_REQUESTED),
            from_status=plan.from_status,
            to_status=plan.to_status,
            actor_type=ActorType.USER,
            actor_id=actor_id,
            note="触发 AI 异常分析",
            detail={"analysis_id": analysis.id, "input_hash": digest},
        )
        write_audit(
            self.session,
            self.repos,
            "exception.analysis_requested",
            resource_type="exception",
            resource_id=case.id,
            actor_id=actor_id,
            before={"status": plan.from_status},
            after={"status": plan.to_status, "analysis_id": analysis.id},
        )

        call = ai_bridge.execute_analysis(self.session, self.repos, analysis.id)
        if not call.get("ok"):
            self._mark_analysis_failed(
                case,
                analysis,
                error_code=str(call.get("error_code") or ai_bridge.LLM_UNAVAILABLE),
                error_message=str(call.get("error_message") or "AI 分析不可用")[:255],
            )
            return {
                "analysis_id": analysis.id,
                "status": str(analysis.status),
                "reused": False,
                "reused_from_id": None,
                "exception_status": case.status,
                "input_hash": digest,
                "error_code": analysis.error_code,
                "error_message": analysis.error_message,
            }

        self._absorb_ai_result(analysis, call.get("result") or {})
        if str(analysis.status) == str(AnalysisStatus.FAILED):
            self._mark_analysis_failed(
                case,
                analysis,
                error_code=str(analysis.error_code or ai_bridge.LLM_UNAVAILABLE),
                error_message=str(analysis.error_message or "AI 分析失败")[:255],
            )
            return {
                "analysis_id": analysis.id,
                "status": str(analysis.status),
                "reused": False,
                "reused_from_id": None,
                "exception_status": case.status,
                "input_hash": digest,
                "error_code": analysis.error_code,
                "error_message": analysis.error_message,
            }

        self.apply_analysis_result(analysis.id, actor_id=actor_id)
        return {
            "analysis_id": analysis.id,
            "status": str(analysis.status),
            "reused": False,
            "reused_from_id": None,
            "exception_status": case.status,
            "input_hash": digest,
            "error_code": None,
        }

    def _clone_reuse(
        self,
        case: ExceptionCase,
        source: AiAnalysis,
        *,
        actor_id: int | None,
    ) -> AiAnalysis:
        clone = AiAnalysis(
            workspace_id=int(self.repos.workspace_id or 0),
            exception_id=case.id,
            analysis_no=next_analysis_no(self.repos),
            task_type=str(source.task_type or AnalysisTaskType.ANALYZE_EXCEPTION),
            status=str(AnalysisStatus.READY),
            triggered_by=actor_id,
            input_hash=source.input_hash,
            model=source.model,
            prompt_version=source.prompt_version,
            output_json=source.output_json,
            raw_output=source.raw_output,
            risk_level_calculated=source.risk_level_calculated,
            tokens_in=source.tokens_in,
            tokens_out=source.tokens_out,
            latency_ms=source.latency_ms,
            reused_from_id=source.id,
            is_replay=bool(source.is_replay),
            started_at=now_naive(),
            finished_at=now_naive(),
        )
        self.repos.analyses.add(clone)
        if str(case.status) == str(ExceptionStatus.CONFIRMING):
            apply_transition(case, EXCEPTION_KIND, ExceptionStatus.ANALYZING)
            apply_transition(case, EXCEPTION_KIND, ExceptionStatus.PROCESSING)
            bump_version(case)
            self.repos.exceptions.save(case)
        elif str(case.status) == str(ExceptionStatus.ANALYZING):
            apply_transition(case, EXCEPTION_KIND, ExceptionStatus.PROCESSING)
            bump_version(case)
            self.repos.exceptions.save(case)
        self.apply_analysis_result(clone.id, actor_id=actor_id)
        return clone

    def retry_analysis(self, analysis_id: int, *, actor_id: int | None = None) -> dict[str, Any]:
        """POST /ai-analyses/{id}/retry：仅 FAILED 可重跑，复用同一个 input_hash。"""
        analysis = self.repos.analyses.get_or_404(analysis_id, "分析任务不存在")
        if str(analysis.status) != str(AnalysisStatus.FAILED):
            raise AppError(
                ErrorCode.STATE_TRANSITION_INVALID,
                f"分析任务状态为 {analysis.status}，只有 FAILED 可以重跑",
                {"analysis_id": analysis.id, "status": analysis.status},
            )
        case = self.get(analysis.exception_id)
        digest = analysis.input_hash or self.input_hash(case)

        from_status = case.status
        if str(case.status) == str(ExceptionStatus.CONFIRMING):
            apply_transition(case, EXCEPTION_KIND, ExceptionStatus.ANALYZING)
            bump_version(case)
            self.repos.exceptions.save(case)
        elif str(case.status) not in {str(ExceptionStatus.ANALYZING), str(ExceptionStatus.PROCESSING)}:
            raise AppError(
                ErrorCode.STATE_TRANSITION_INVALID,
                f"异常状态为 {case.status}，无法重跑分析",
                {"from": case.status, "to": str(ExceptionStatus.ANALYZING)},
            )

        rerun = AiAnalysis(
            workspace_id=int(self.repos.workspace_id or 0),
            exception_id=case.id,
            analysis_no=next_analysis_no(self.repos),
            task_type=str(analysis.task_type or AnalysisTaskType.ANALYZE_EXCEPTION),
            status=str(AnalysisStatus.RUNNING),
            triggered_by=actor_id,
            input_hash=digest,
            model=analysis.model or get_settings().llm_model,
            prompt_version=analysis.prompt_version or "v1",
            reused_from_id=analysis.id,
            started_at=now_naive(),
        )
        self.repos.analyses.add(rerun)
        add_event(
            self.session,
            self.repos,
            exception_id=case.id,
            event_type=str(ExceptionEventType.ANALYSIS_REQUESTED),
            from_status=from_status,
            to_status=case.status,
            actor_type=ActorType.USER,
            actor_id=actor_id,
            note="重跑上一次失败的分析（复用 input_hash）",
            detail={"analysis_id": rerun.id, "retry_of": analysis.id, "input_hash": digest},
        )
        write_audit(
            self.session,
            self.repos,
            "exception.analysis_retried",
            resource_type="exception",
            resource_id=case.id,
            actor_id=actor_id,
            after={"analysis_id": rerun.id, "retry_of": analysis.id},
        )

        call = ai_bridge.execute_analysis(self.session, self.repos, rerun.id)
        if not call.get("ok"):
            self._mark_analysis_failed(
                case,
                rerun,
                error_code=str(call.get("error_code") or ai_bridge.LLM_UNAVAILABLE),
                error_message=str(call.get("error_message") or "AI 分析不可用")[:255],
            )
        else:
            self._absorb_ai_result(rerun, call.get("result") or {})
            if str(rerun.status) == str(AnalysisStatus.FAILED):
                self._mark_analysis_failed(
                    case,
                    rerun,
                    error_code=str(rerun.error_code or ai_bridge.LLM_UNAVAILABLE),
                    error_message=str(rerun.error_message or "AI 分析失败")[:255],
                )
            else:
                self.apply_analysis_result(rerun.id, actor_id=actor_id)

        return {
            "analysis_id": rerun.id,
            "status": str(rerun.status),
            "reused_from_id": rerun.reused_from_id,
            "exception_status": case.status,
            "error_code": rerun.error_code,
            "error_message": rerun.error_message,
        }

    def _absorb_ai_result(self, analysis: AiAnalysis, result: dict[str, Any]) -> None:
        """把 AI 返回体与 AI 层写入的行做一次归一（两边任一处有结果都算成功）。"""
        payload = result or {}
        output = payload.get("output") or payload.get("output_json")
        if output is None and payload.get("summary"):
            output = payload
        if output is not None and analysis.output_json is None:
            analysis.output_json = output
        for field in (
            "raw_output",
            "model",
            "prompt_version",
            "tokens_in",
            "tokens_out",
            "latency_ms",
            "risk_level_calculated",
            "is_replay",
            "error_code",
            "error_message",
        ):
            value = payload.get(field)
            if value is not None:
                setattr(analysis, field, value)
        status = str(payload.get("status") or analysis.status or "").upper()
        if status not in {member.value for member in AnalysisStatus}:
            status = str(AnalysisStatus.READY if analysis.output_json else AnalysisStatus.FAILED)
        analysis.status = status
        if status == str(AnalysisStatus.READY):
            analysis.finished_at = to_naive_utc(analysis.finished_at) or now_naive()
        elif status == str(AnalysisStatus.FAILED):
            analysis.finished_at = to_naive_utc(analysis.finished_at) or now_naive()
            analysis.error_code = analysis.error_code or ai_bridge.LLM_UNAVAILABLE
        self.repos.analyses.save(analysis)

    def _mark_analysis_failed(
        self,
        case: ExceptionCase,
        analysis: AiAnalysis,
        *,
        error_code: str,
        error_message: str,
    ) -> None:
        analysis.status = str(AnalysisStatus.FAILED)
        analysis.error_code = error_code
        analysis.error_message = error_message
        analysis.finished_at = now_naive()
        bump_version(analysis)
        self.repos.analyses.save(analysis)

        if str(case.status) == str(ExceptionStatus.ANALYZING):
            plan = apply_transition(case, EXCEPTION_KIND, ExceptionStatus.CONFIRMING)
            bump_version(case)
            self.repos.exceptions.save(case)
            add_event(
                self.session,
                self.repos,
                exception_id=case.id,
                event_type=str(ExceptionEventType.ANALYSIS_FAILED),
                from_status=plan.from_status,
                to_status=plan.to_status,
                actor_type=ActorType.SYSTEM,
                note=error_message,
                detail={"analysis_id": analysis.id, "error_code": error_code},
            )
            write_audit(
                self.session,
                self.repos,
                "exception.analysis_failed",
                resource_type="exception",
                resource_id=case.id,
                actor_type=ActorType.SYSTEM,
                before={"status": plan.from_status},
                after={"status": plan.to_status, "error_code": error_code},
                source=AuditSource.SYSTEM,
            )
        else:
            add_event(
                self.session,
                self.repos,
                exception_id=case.id,
                event_type=str(ExceptionEventType.ANALYSIS_FAILED),
                from_status=case.status,
                to_status=case.status,
                actor_type=ActorType.SYSTEM,
                note=error_message,
                detail={"analysis_id": analysis.id, "error_code": error_code},
            )
            write_audit(
                self.session,
                self.repos,
                "exception.analysis_failed",
                resource_type="exception",
                resource_id=case.id,
                actor_type=ActorType.SYSTEM,
                after={"analysis_id": analysis.id, "error_code": error_code},
                source=AuditSource.SYSTEM,
            )

    def apply_analysis_result(
        self,
        analysis_id: int,
        *,
        actor_id: int | None = None,
    ) -> dict[str, Any]:
        """AI READY → PROCESSING：规则定级（覆盖 LLM）、建议转审批单、写 ANALYSIS_READY。"""
        from app.services.approvals import build_approvals_from_analysis  # 局部导入避免循环

        analysis = self.repos.analyses.get_or_404(analysis_id, "分析任务不存在")
        if str(analysis.status) != str(AnalysisStatus.READY):
            raise AppError(
                ErrorCode.STATE_TRANSITION_INVALID,
                f"分析任务状态为 {analysis.status}，不能落库",
                {"analysis_id": analysis.id, "status": analysis.status},
            )
        case = self.get(analysis.exception_id)
        order = self.repos.orders.get_or_404(case.order_id, "订单不存在")

        from_status = case.status
        if str(case.status) == str(ExceptionStatus.ANALYZING):
            apply_transition(case, EXCEPTION_KIND, ExceptionStatus.PROCESSING)
        elif str(case.status) != str(ExceptionStatus.PROCESSING):
            raise AppError(
                ErrorCode.STATE_TRANSITION_INVALID,
                f"异常状态为 {case.status}，不能接收分析结果",
                {"from": case.status, "to": str(ExceptionStatus.PROCESSING)},
            )

        output = analysis.output_json or {}
        self._apply_output_to_case(case, order, output)
        approvals = build_approvals_from_analysis(self.repos, analysis, actor_id=actor_id)
        bump_version(case)
        self.repos.exceptions.save(case)
        analysis.risk_level_calculated = case.level
        self.repos.analyses.save(analysis)

        add_event(
            self.session,
            self.repos,
            exception_id=case.id,
            event_type=str(ExceptionEventType.ANALYSIS_READY),
            from_status=from_status,
            to_status=case.status,
            actor_type=ActorType.AI,
            actor_id=actor_id,
            note=f"分析完成，规则定级 {case.level}（{case.risk_score}）",
            detail={
                "analysis_id": analysis.id,
                "risk_score": case.risk_score,
                "level": case.level,
                "risk_factors": case.risk_factors_json,
                "approval_ids": [approval.id for approval in approvals],
            },
        )
        write_audit(
            self.session,
            self.repos,
            "exception.analysis_ready",
            resource_type="exception",
            resource_id=case.id,
            actor_type=ActorType.SYSTEM,
            actor_id=actor_id,
            before={"status": from_status},
            after={
                "status": case.status,
                "analysis_id": analysis.id,
                "level": case.level,
                "risk_score": case.risk_score,
                "approval_ids": [approval.id for approval in approvals],
            },
            source=AuditSource.APPROVED_AI,
        )
        return {
            "analysis_id": analysis.id,
            "exception_id": case.id,
            "status": case.status,
            "level": case.level,
            "risk_score": case.risk_score,
            "risk_factors": case.risk_factors_json,
            "approval_ids": [approval.id for approval in approvals],
        }

    def _apply_output_to_case(self, case: ExceptionCase, order: Any, output: dict[str, Any]) -> None:
        """把 LLM 的定性内容写回主单，但数字/等级一律用规则重算（§8.6 硬约束）。"""
        root_cause = output.get("root_cause") or {}
        code = str(root_cause.get("code") or "").upper()
        if code:
            case.root_cause_code = code if code in {member.value for member in RootCauseCode} else "UNKNOWN"
        note = root_cause.get("note")
        if note:
            case.root_cause_note = str(note)[:255]
        summary = str(output.get("summary") or "").strip()
        if summary:
            case.impact_summary = summary[:255]
        eta_flow.refresh_case_impact(self.repos, case, order, eta_at=order.current_eta_at)

    # --- 承运商消息 -------------------------------------------------------
    def add_message(
        self,
        exception_id: int,
        *,
        raw_text: str,
        channel: str = MessageChannel.MANUAL_PASTE,
        sender_name: str | None = None,
        received_at: datetime | str | None = None,
        expected_version: int | None = None,
        actor_id: int | None = None,
    ) -> dict[str, Any]:
        case = self.get(exception_id)
        check_version(case, expected_version, "异常单")
        if str(case.status) in {str(ExceptionStatus.CLOSED), str(ExceptionStatus.RESOLVED)}:
            raise AppError(
                ErrorCode.STATE_TRANSITION_INVALID,
                f"异常单已 {case.status}，不再接收承运商消息",
                {"from": case.status},
            )
        channel_value = str(channel or MessageChannel.MANUAL_PASTE).upper()
        if channel_value not in {member.value for member in MessageChannel}:
            channel_value = str(MessageChannel.MANUAL_PASTE)
        text = require_text(raw_text, "raw_text", max_len=4000)

        message = CarrierMessage(
            workspace_id=int(self.repos.workspace_id or 0),
            exception_id=case.id,
            order_id=case.order_id,
            channel=channel_value,
            sender_name=sender_name,
            sender_role="CARRIER",
            raw_text=text,
            received_at=parse_iso_naive(received_at) or now_naive(),
            parse_status=str(ParseStatus.PENDING),
            created_by=actor_id,
        )
        self.repos.messages.add(message)

        transitioned = False
        if str(case.status) == str(ExceptionStatus.DETECTED):
            plan = apply_transition(case, EXCEPTION_KIND, ExceptionStatus.CONFIRMING)
            bump_version(case)
            self.repos.exceptions.save(case)
            transitioned = True
            add_event(
                self.session,
                self.repos,
                exception_id=case.id,
                event_type=str(ExceptionEventType.STATUS_CHANGED),
                from_status=plan.from_status,
                to_status=plan.to_status,
                actor_type=ActorType.SYSTEM,
                actor_id=actor_id,
                note="录入承运商消息，异常进入确认中",
            )
        add_event(
            self.session,
            self.repos,
            exception_id=case.id,
            event_type=str(ExceptionEventType.MESSAGE_ADDED),
            from_status=case.status,
            to_status=case.status,
            actor_type=ActorType.USER,
            actor_id=actor_id,
            note=text[:200],
            detail={"message_id": message.id, "channel": channel_value},
        )
        write_audit(
            self.session,
            self.repos,
            "exception.message_added",
            resource_type="exception",
            resource_id=case.id,
            actor_id=actor_id,
            after={"message_id": message.id, "channel": channel_value, "parse_status": message.parse_status},
        )

        call = ai_bridge.parse_message(self.session, self.repos, message.id)
        parse_result: dict[str, Any] | None = None
        if call.get("ok"):
            parse_result = self._normalize_parse_result(
                call.get("result") or {}, existing=message.parse_result_json
            )
            message.parse_status = str(ParseStatus.PARSED)
            message.parse_result_json = parse_result
            message.parser_version = str((call.get("result") or {}).get("prompt_version") or "v1")[:32]
            message.parse_error = None
        else:
            # AI 不可用：仍用确定性兜底把 §11.2 表 2 的字段落库，前端对照表不能只剩一行
            parse_result = self._fallback_parse_result(case, message)
            message.parse_status = str(ParseStatus.FAILED)
            message.parse_result_json = parse_result
            message.parse_error = str(call.get("error_message") or "AI 解析不可用")[:255]
        self.repos.messages.save(message)

        eta_payload: dict[str, Any] | None = None
        if parse_result is not None and (
            call.get("ok") or parse_result.get("estimated_recovery_at") or parse_result.get("status") != "UNKNOWN"
        ):
            eta_payload = self._apply_parse_to_eta(case, parse_result, actor_id=actor_id)
            write_audit(
                self.session,
                self.repos,
                "exception.message_parsed",
                resource_type="exception",
                resource_id=case.id,
                actor_id=actor_id,
                after={
                    "message_id": message.id,
                    "parse_status": str(message.parse_status),
                    "parse_result": parse_result,
                },
                source=AuditSource.SYSTEM,
            )

        return {
            "message_id": message.id,
            "parse_status": str(message.parse_status),
            "exception_id": case.id,
            "exception_status": case.status,
            "transitioned": transitioned,
            "parse_result": message.parse_result_json,
            "eta": eta_payload,
            "error_code": None if call.get("ok") else call.get("error_code"),
            "error_message": None if call.get("ok") else message.parse_error,
        }

    def _normalize_parse_result(
        self,
        result: dict[str, Any],
        *,
        existing: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """归一 §11.2 表 2 的 T1 输出字段。

        兼容三种形态：AI 层返回的 {"status","output",...}、直接字段、以及已落库的 parse_result_json。
        注意：任务级 status（PARSED/FAILED）绝不能写进"车辆状态"字段。
        """
        payload: dict[str, Any] = {}
        if isinstance(existing, dict):
            payload.update({key: existing.get(key) for key in PARSE_FIELDS if key in existing})
            if isinstance(existing.get("meta"), dict):
                payload["meta"] = existing["meta"]
        nested = result.get("output")
        if isinstance(nested, dict):
            payload.update(nested)
        for source in (result.get("parse_result"), result.get("result")):
            if isinstance(source, dict):
                payload.update({key: value for key, value in source.items() if key != "status" or "location" in source})
        payload.update({key: result.get(key) for key in PARSE_FIELDS if key in result and key != "status"})
        if "status" in result and isinstance(nested, dict):
            payload["status"] = nested.get("status", payload.get("status"))

        meta = dict(payload.get("meta") or {})
        for key in ("model", "is_replay", "prompt_version"):
            if result.get(key) is not None:
                meta[key] = result[key]
        if meta:
            payload["meta"] = meta

        cleaned: dict[str, Any] = {}
        for key, value in payload.items():
            if value is None or isinstance(value, (str, int, float, bool, list, dict)):
                cleaned[str(key)] = value
            else:
                cleaned[str(key)] = str(value)
        recovered = parse_iso_naive(cleaned.get("estimated_recovery_at"))
        if recovered is not None:
            cleaned["estimated_recovery_at"] = read_models.iso(recovered)
        if not cleaned.get("status"):
            cleaned["status"] = "UNKNOWN"
        cleaned.setdefault("missing_info", [])
        return cleaned

    def _fallback_parse_result(self, case: ExceptionCase, message: CarrierMessage) -> dict[str, Any]:
        """AI 不可用时的确定性兜底：从消息文本 + 事实快照推导可展示的字段。"""
        text = message.raw_text or ""
        if any(keyword in text for keyword in ("等待配件", "等配件", "等件", "缺件", "没有配件")):
            vehicle_status = "WAITING_PARTS"
        elif any(keyword in text for keyword in ("爆胎", "故障", "维修", "修理", "坏了", "抛锚", "事故")):
            vehicle_status = "REPAIRING"
        elif any(keyword in text for keyword in ("恢复", "已修好", "正常", "继续", "出发", "已到达")):
            vehicle_status = "MOVING"
        else:
            vehicle_status = "UNKNOWN"

        order = self.repos.orders.get(case.order_id)
        vehicle = self.repos.vehicles.get(case.vehicle_id) if case.vehicle_id else None
        location = (vehicle.current_city if vehicle else None) or (order.dest_city if order else None)
        recovery = self._derive_recovery_at(text, message.received_at)
        return {
            "exception_type": case.type,
            "location": location,
            "status": vehicle_status,
            "estimated_recovery_at": read_models.iso(recovery),
            "confidence": 0.3,
            "missing_info": ["AI 不可用，字段由确定性规则兜底"],
            "meta": {"model": "template", "is_replay": True, "prompt_version": "fallback"},
        }

    def _derive_recovery_at(self, text: str, received_at: datetime | None) -> datetime | None:
        """从"预计晚上 8 点恢复"这类表述里解析相对时间（确定性兜底，不调模型）。"""
        match = TIME_HINT_RE.search(text or "")
        if match is None:
            return None
        period, hour_text, colon_minute, dian_minute = match.groups()
        hour = int(hour_text)
        minute = int(colon_minute or dian_minute or 0)
        if hour > 23 or minute > 59:
            return None
        if period in {"下午", "晚上", "傍晚"} and hour < 12:
            hour += 12
        if period == "凌晨" and hour >= 12:
            hour -= 12
        base = to_naive_utc(received_at) or now_naive()
        candidate = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate < base:
            candidate += timedelta(days=1)
        return candidate

    def _apply_parse_to_eta(
        self,
        case: ExceptionCase,
        parse_result: dict[str, Any],
        *,
        actor_id: int | None = None,
    ) -> dict[str, Any] | None:
        order = self.repos.orders.get(case.order_id)
        if order is None:
            return None
        vehicle_status = str(parse_result.get("status") or "").upper()
        if case.vehicle_id and vehicle_status in {"REPAIRING", "WAITING_PARTS", "BREAKDOWN"}:
            vehicle = self.repos.vehicles.get(case.vehicle_id)
            if vehicle is not None and str(vehicle.status) != str(VehicleStatus.REPAIRING):
                vehicle.status = str(VehicleStatus.REPAIRING)
                bump_version(vehicle)
                self.repos.vehicles.save(vehicle)
        recovery = parse_iso_naive(parse_result.get("estimated_recovery_at"))
        result = eta_flow.recalc_order_eta(self.repos, order, repair_recovery_at=recovery)
        eta_flow.refresh_case_impact(self.repos, case, order, eta_at=result.eta_at)
        add_event(
            self.session,
            self.repos,
            exception_id=case.id,
            event_type=str(ExceptionEventType.ETA_UPDATED),
            from_status=case.status,
            to_status=case.status,
            actor_type=ActorType.SYSTEM,
            actor_id=actor_id,
            note=f"承运商消息解析后重算 ETA（{result.method}）",
            detail={"eta_method": result.method, "eta_at": read_models.iso(result.eta_at)},
        )
        write_audit(
            self.session,
            self.repos,
            "order.eta_recalculated",
            resource_type="order",
            resource_id=order.id,
            actor_type=ActorType.SYSTEM,
            actor_id=actor_id,
            after={
                "eta_at": read_models.iso(result.eta_at),
                "eta_method": result.method,
                "remaining_km": result.remaining_km,
                "avg_speed_kmh": result.avg_speed_kmh,
                "sla_delay_minutes": case.sla_delay_minutes,
                "sla_breached": bool(case.sla_breached),
            },
            source=AuditSource.SYSTEM,
        )
        return result.as_dict()

    # --- 解决与关闭 -------------------------------------------------------
    def resolve(
        self,
        exception_id: int,
        *,
        note: str,
        expected_version: int | None = None,
        actor_id: int | None = None,
        system: bool = False,
    ) -> ExceptionCase:
        case = self.get(exception_id)
        check_version(case, expected_version, "异常单")
        note_text = require_text(note, "note", max_len=500)
        order = self.repos.orders.get(case.order_id)

        if order is not None:
            eta = eta_flow.recalc_order_eta(self.repos, order)
            eta_flow.refresh_case_impact(self.repos, case, order, eta_at=eta.eta_at)

        plan = apply_transition(case, EXCEPTION_KIND, ExceptionStatus.RESOLVED)
        case.resolved_at = now_naive()
        bump_version(case)
        self.repos.exceptions.save(case)
        actor_type = ActorType.SYSTEM if system else ActorType.USER
        add_event(
            self.session,
            self.repos,
            exception_id=case.id,
            event_type=str(ExceptionEventType.STATUS_CHANGED),
            from_status=plan.from_status,
            to_status=plan.to_status,
            actor_type=actor_type,
            actor_id=actor_id,
            note=note_text,
        )
        add_event(
            self.session,
            self.repos,
            exception_id=case.id,
            event_type=str(ExceptionEventType.ETA_UPDATED),
            from_status=case.status,
            to_status=case.status,
            actor_type=actor_type,
            actor_id=actor_id,
            note="解决时重算 ETA 与 SLA",
            detail={
                "eta_at": read_models.iso(order.current_eta_at) if order else None,
                "sla_delay_minutes": case.sla_delay_minutes,
                "sla_breached": bool(case.sla_breached),
                "level": case.level,
            },
        )
        write_audit(
            self.session,
            self.repos,
            "exception.resolved",
            resource_type="exception",
            resource_id=case.id,
            actor_type=actor_type,
            actor_id=actor_id,
            before={"status": plan.from_status},
            after={"status": plan.to_status, "note": note_text},
            source=AuditSource.SYSTEM if system else AuditSource.MANUAL,
        )
        return case

    def close(
        self,
        exception_id: int,
        *,
        reason_code: str | None = None,
        note: str | None = None,
        expected_version: int | None = None,
        actor_id: int | None = None,
        forced: bool = False,
        system: bool = False,
    ) -> ExceptionCase:
        case = self.get(exception_id)
        check_version(case, expected_version, "异常单")
        if state_machine.is_terminal(EXCEPTION_KIND, case.status):
            raise AppError(
                ErrorCode.STATE_TRANSITION_INVALID,
                "异常单已关闭，终态不可逆（ADR-A11）",
                {"from": case.status, "to": str(ExceptionStatus.CLOSED)},
            )
        in_progress = {str(ExceptionStatus.CONFIRMING), str(ExceptionStatus.ANALYZING)}
        if str(case.status) in in_progress and not forced:
            raise AppError(
                ErrorCode.STATE_TRANSITION_INVALID,
                "处理中的异常需要强制关闭权限（ADMIN+）",
                {"from": case.status, "to": str(ExceptionStatus.CLOSED), "required": "FORCED_CLOSE"},
            )
        reason = str(reason_code or "").upper()
        if reason not in VALID_CLOSE_REASONS:
            reason = "MANUAL"
        if forced and not (note or "").strip():
            raise validation_error("强制关闭必须填写 note")

        plan = apply_transition(case, EXCEPTION_KIND, ExceptionStatus.CLOSED, reason=reason)
        case.closed_at = now_naive()
        case.close_reason = reason
        if not case.resolved_at and str(plan.from_status) == str(ExceptionStatus.RESOLVED):
            case.resolved_at = case.closed_at
        bump_version(case)
        self.repos.exceptions.save(case)
        actor_type = ActorType.SYSTEM if system else ActorType.USER
        add_event(
            self.session,
            self.repos,
            exception_id=case.id,
            event_type=str(ExceptionEventType.CLOSED),
            from_status=plan.from_status,
            to_status=plan.to_status,
            actor_type=actor_type,
            actor_id=actor_id,
            note=note,
            detail={"close_reason": reason, "forced": bool(forced)},
        )
        write_audit(
            self.session,
            self.repos,
            "exception.forced_close" if forced else "exception.closed",
            resource_type="exception",
            resource_id=case.id,
            actor_type=actor_type,
            actor_id=actor_id,
            before={"status": plan.from_status},
            after={"status": plan.to_status, "close_reason": reason, "forced": bool(forced)},
            source=AuditSource.SYSTEM if system else AuditSource.MANUAL,
        )
        return case

    # --- 只读 -------------------------------------------------------------
    def detail(self, exception_id: int) -> dict[str, Any]:
        case = self.get(exception_id)
        facts = read_models.exception_facts(self.repos, case.id)
        latest_analysis = self.repos.analyses.latest_for_case(case.id)
        order = self.repos.orders.get(case.order_id)

        detail = {
            "id": case.id,
            "case_no": case.case_no,
            "order_id": case.order_id,
            "customer_id": case.customer_id,
            "vehicle_id": case.vehicle_id,
            "carrier_id": case.carrier_id,
            "type": case.type,
            "level": case.level,
            "status": case.status,
            "detected_by": case.detected_by,
            "detection_rule": case.detection_rule,
            "occurred_at": read_models.iso(case.occurred_at),
            "stall_since": read_models.iso(case.stall_since),
            "root_cause": {
                "code": case.root_cause_code,
                "note": case.root_cause_note,
            },
            "impact_summary": case.impact_summary,
            "promised_delivery_at": read_models.iso(case.promised_delivery_at),
            "current_eta_at": read_models.iso(order.current_eta_at) if order else None,
            "expected_eta_at": read_models.iso(case.expected_eta_at),
            "sla_delay_minutes": case.sla_delay_minutes,
            "sla_breached": bool(case.sla_breached),
            "risk_score": case.risk_score,
            "risk_factors": case.risk_factors_json or [],
            "assigned_to": case.assigned_to,
            "resolved_at": read_models.iso(case.resolved_at),
            "closed_at": read_models.iso(case.closed_at),
            "close_reason": case.close_reason,
            "merged_count": case.merged_count,
            "version": case.version,
            "created_at": read_models.iso(case.created_at),
            "updated_at": read_models.iso(case.updated_at),
            "order": facts.get("order"),
            "customer": facts.get("customer"),
            "vehicle": facts.get("vehicle"),
            "sla": facts.get("sla"),
            "tracking_events": facts.get("tracking_events"),
            "latest_carrier_message": facts.get("latest_carrier_message"),
            "history": facts.get("history"),
            "latest_analysis": self.analysis_summary(latest_analysis),
            "counts": {
                "messages": len(self.repos.messages.list_for_case(case.id)),
                "followups_open": self.repos.followups.count_open(case.id),
                "notifications": len(self.repos.notifications.list_for_case(case.id)),
                "approvals_pending": len(self.repos.approvals.list_pending(case.id)),
            },
        }
        return detail

    @staticmethod
    def analysis_summary(analysis: AiAnalysis | None) -> dict[str, Any] | None:
        if analysis is None:
            return None
        return {
            "id": analysis.id,
            "analysis_no": analysis.analysis_no,
            "task_type": analysis.task_type,
            "status": analysis.status,
            "is_replay": bool(analysis.is_replay),
            "risk_level_calculated": analysis.risk_level_calculated,
            "error_code": analysis.error_code,
            "error_message": analysis.error_message,
            "reused_from_id": analysis.reused_from_id,
            "started_at": read_models.iso(analysis.started_at),
            "finished_at": read_models.iso(analysis.finished_at),
            "summary": (analysis.output_json or {}).get("summary"),
            "root_cause": (analysis.output_json or {}).get("root_cause"),
            "impact": (analysis.output_json or {}).get("impact"),
            "suggestions": (analysis.output_json or {}).get("suggestions") or [],
        }

    def timeline(
        self,
        exception_id: int,
        *,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[dict[str, Any]], int]:
        case = self.get(exception_id)
        events, total = self.repos.exception_events.list_for_case(case.id, page=page, page_size=page_size)
        return [self.serialize_event(event) for event in events], total

    @staticmethod
    def serialize_event(event: Any) -> dict[str, Any]:
        return {
            "id": event.id,
            "exception_id": event.exception_id,
            "event_type": event.event_type,
            "actor_type": event.actor_type,
            "actor_id": event.actor_id,
            "from_status": event.from_status,
            "to_status": event.to_status,
            "note": event.note,
            "detail": event.detail_json,
            "occurred_at": read_models.iso(event.occurred_at),
        }

    def list_messages(self, exception_id: int) -> list[dict[str, Any]]:
        case = self.get(exception_id)
        return [self.serialize_message(message) for message in self.repos.messages.list_for_case(case.id)]

    @staticmethod
    def serialize_message(message: CarrierMessage) -> dict[str, Any]:
        return {
            "id": message.id,
            "exception_id": message.exception_id,
            "order_id": message.order_id,
            "channel": message.channel,
            "sender_name": message.sender_name,
            "sender_role": message.sender_role,
            "raw_text": message.raw_text,
            "received_at": read_models.iso(message.received_at),
            "parse_status": message.parse_status,
            "parse_result": message.parse_result_json,
            "parser_version": message.parser_version,
            "parse_error": message.parse_error,
            "created_at": read_models.iso(message.created_at),
        }

    # --- 供 tick 使用 -----------------------------------------------------
    def auto_close_resolved(self, *, hours: int = 24, moment: datetime | None = None) -> list[int]:
        now = to_naive_utc(moment) or now_naive()
        threshold = now - timedelta(hours=hours)
        closed: list[int] = []
        for case in self.repos.exceptions.all(
            filters=[ExceptionCase.status == str(ExceptionStatus.RESOLVED)]
        ):
            order = self.repos.orders.get(case.order_id)
            delivered_at = to_naive_utc(order.delivered_at) if order else None
            if delivered_at is not None and delivered_at <= threshold:
                self.close(
                    case.id,
                    reason_code="DELIVERED",
                    note=f"送达后 {hours} 小时自动关闭",
                    expected_version=None,
                    actor_id=None,
                    system=True,
                )
                closed.append(case.id)
        return closed

    def pending_approval_count(self, exception_id: int) -> int:
        return len(
            [
                approval
                for approval in self.repos.approvals.list_for_case(exception_id)
                if str(approval.status) == str(ApprovalStatus.PENDING)
            ]
        )


__all__ = ["ANALYSIS_TIMEOUT_SECONDS", "ExceptionService", "REUSE_WINDOW_MINUTES", "search_exceptions"]
