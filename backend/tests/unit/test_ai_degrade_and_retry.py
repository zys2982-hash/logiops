"""失败重试与 AI 降级（§11.8）：

- ai_bridge 在 AI 层缺失 / 抛错时返回 ok=False + error_code，绝不向上抛异常；
- 执行失败的审批单 → FAILED，可 POST /execute 重试成功；
- run_tick 的推进与自动关闭可复现。
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.core.clock import state as clock_state
from app.core.errors import AppError
from app.models.enums import AnalysisStatus, ApprovalStatus, ExceptionStatus, ParseStatus
from app.services import ai_bridge, tick
from app.services.approvals import ApprovalExecutor
from app.services.exceptions import ExceptionService
from tests.unit import _support


def test_ai_bridge_degrades_when_runner_missing(monkeypatch):
    monkeypatch.setattr(ai_bridge, "_load_runner", lambda: None)
    call = ai_bridge.execute_analysis(None, None, 1)
    assert call["ok"] is False
    assert call["error_code"] == ai_bridge.LLM_UNAVAILABLE
    parse = ai_bridge.parse_message(None, None, 2)
    assert parse["ok"] is False
    draft = ai_bridge.draft_notice(None, None, 3)
    assert draft["ok"] is False


def test_ai_bridge_degrades_when_runner_raises(monkeypatch):
    class Boom:
        def execute_analysis(self, *args, **kwargs):
            raise TimeoutError("模型超时")

    monkeypatch.setattr(ai_bridge, "_load_runner", lambda: Boom())
    call = ai_bridge.execute_analysis(None, None, 1)
    assert call["ok"] is False
    assert call["error_code"] == ai_bridge.LLM_UNAVAILABLE
    assert "模型超时" in call["error_message"]

    class BadSchema:
        def parse_message(self, *args, **kwargs):
            raise ValueError("schema validation failed")

    monkeypatch.setattr(ai_bridge, "_load_runner", lambda: BadSchema())
    assert ai_bridge.parse_message(None, None, 1)["error_code"] == ai_bridge.AI_OUTPUT_INVALID


def test_request_analysis_degrades_without_blocking(db_session, bootstrap, monkeypatch):
    """AI 不可用：ai_analysis=FAILED/LLM_UNAVAILABLE，异常保持「处理中」，不抛异常。"""
    repos = _support.repos_for(db_session, bootstrap)
    case = _support.detected_exception(repos, bootstrap)
    service = ExceptionService(repos)
    service.confirm(case.id, expected_version=case.version, actor_id=None)

    monkeypatch.setattr(ai_bridge, "_load_runner", lambda: None)
    result = service.request_analysis(case.id, expected_version=case.version, actor_id=None)

    assert result["status"] == str(AnalysisStatus.FAILED)
    assert result["error_code"] == ai_bridge.LLM_UNAVAILABLE
    analysis = repos.analyses.get(result["analysis_id"])
    assert analysis is not None and analysis.status == str(AnalysisStatus.FAILED)
    assert analysis.finished_at is not None
    assert case.status == str(ExceptionStatus.PROCESSING)  # 4 状态模型：分析失败不改异常状态

    events, _ = repos.exception_events.list_for_case(case.id)
    assert "ANALYSIS_FAILED" in {event.event_type for event in events}


def test_request_analysis_reuses_ready_result_within_15_minutes(db_session, bootstrap, monkeypatch):
    repos = _support.repos_for(db_session, bootstrap)
    case = _support.detected_exception(repos, bootstrap)
    service = ExceptionService(repos)
    service.confirm(case.id, expected_version=case.version, actor_id=None)
    monkeypatch.setattr(ai_bridge, "_load_runner", lambda: None)

    first = service.request_analysis(case.id, expected_version=case.version, actor_id=None)
    # 让上一次分析变成 READY + input_hash 一致（模拟 AI 成功过）
    analysis = repos.analyses.get(first["analysis_id"])
    analysis.status = str(AnalysisStatus.READY)
    repos.analyses.save(analysis)

    digest = service.input_hash(case)
    analysis.input_hash = digest
    repos.analyses.save(analysis)

    second = service.request_analysis(case.id, expected_version=case.version, actor_id=None)
    assert second["reused"] is True
    assert second["reused_from_id"] == analysis.id
    assert second["status"] == str(AnalysisStatus.READY)
    clone = repos.analyses.get(second["analysis_id"])
    assert clone.reused_from_id == analysis.id
    assert case.status == str(ExceptionStatus.PROCESSING)  # 复用即视为已有结论


def test_add_message_degrades_without_ai(db_session, bootstrap, monkeypatch):
    repos = _support.repos_for(db_session, bootstrap)
    case = _support.detected_exception(repos, bootstrap)
    monkeypatch.setattr(ai_bridge, "_load_runner", lambda: None)

    result = ExceptionService(repos).add_message(
        case.id,
        raw_text="车在济南爆胎了，预计晚上8点恢复",
        expected_version=case.version,
        actor_id=None,
    )
    assert result["parse_status"] == str(ParseStatus.FAILED)
    assert result["error_code"] == ai_bridge.LLM_UNAVAILABLE
    assert result["exception_status"] == str(ExceptionStatus.DETECTED)  # 4 状态模型：录消息不自动推进
    message = repos.messages.get(result["message_id"])
    assert message is not None and message.parse_error


def test_failed_approval_execution_can_be_retried(db_session, bootstrap):
    """执行失败 → FAILED（无业务副作用）；修好 payload 后 retry 成功。"""
    repos = _support.repos_for(db_session, bootstrap)
    order = _support.new_order(repos, bootstrap)  # CREATED，尚无 ETA
    case = ExceptionService(repos).create_manual(
        order_id=order.id,
        type="DELAY_RISK",
        occurred_at=bootstrap["base_time"],
        note="手工建单：验证失败重试",
    )
    assert case.expected_eta_at is None
    approval = _support.make_approval(repos, case, action="UPDATE_ETA", ai_payload={})
    executor = ApprovalExecutor(repos)

    failed = executor.approve(approval.id, actor_id=None, expected_version=approval.version)
    assert failed["status"] == str(ApprovalStatus.FAILED)
    assert failed["execution_result"]["error_code"] == "VALIDATION_ERROR"
    assert repos.orders.get(order.id).current_eta_at is None  # 失败不留业务副作用
    events, _ = repos.exception_events.list_for_case(case.id)
    assert "EXECUTE_FAILED" in {event.event_type for event in events}

    # 修好 payload 后重试成功，retry_count 递增
    approval.final_payload_json = {
        "eta_at": (bootstrap["base_time"] + timedelta(hours=2)).isoformat(),
        "reason": "修好后重试",
    }
    repos.approvals.save(approval)
    retried = executor.retry(approval.id, actor_id=None)
    assert retried["status"] == str(ApprovalStatus.EXECUTED)
    assert approval.retry_count == 1
    assert repos.orders.get(order.id).current_eta_at is not None

    # 已 EXECUTED 的单子不能再重试
    with pytest.raises(AppError):
        executor.retry(approval.id, actor_id=None)


def test_run_tick_delivers_but_never_auto_closes(db_session, bootstrap):
    """推进时钟：只会送到，**异常绝不自动收口**（2026-10-06：解决 / 关闭只由人工点）。"""
    repos = _support.repos_for(db_session, bootstrap)
    case = _support.detected_exception(repos, bootstrap, stall_minutes=130)
    assert case.status == str(ExceptionStatus.DETECTED)

    service = ExceptionService(repos)
    service.confirm(case.id, expected_version=case.version, actor_id=None)
    analysis = _support.make_analysis(repos, case, output=_support.ai_output())
    service.apply_analysis_result(analysis.id)
    assert case.status == str(ExceptionStatus.PROCESSING)

    result = tick.advance_until_delivered(db_session, repos, workspace_id=bootstrap["workspace_id"])
    assert result["stopped_reason"] == "EXCEPTION_NEEDS_MANUAL_STEP", result
    assert result["ticks"] >= 1
    assert result["advanced_minutes"] > 0
    assert result["target_order_no"]
    assert result["target_exception_no"] == case.case_no

    loaded = repos.exceptions.get(case.id)
    assert loaded is not None
    # 送到就停：异常还开着，等人工处置（没有 closed_at / close_reason）
    assert loaded.status == str(ExceptionStatus.PROCESSING)
    assert loaded.closed_at is None
    assert loaded.resolved_at is None
    order = repos.orders.get(case.order_id)
    assert order is not None and order.status in {"DELIVERED", "CLOSED"}

    # 幂等：再推一次不报错，状态不变
    again = tick.advance_until_delivered(db_session, repos, workspace_id=bootstrap["workspace_id"])
    assert again["ticks"] == 0
    assert again["final_exception_status"] == str(ExceptionStatus.PROCESSING)

    # 只有 close_target=True（演示按钮 = 人工起点）那次才收口
    forced = tick.advance_until_delivered(
        db_session, repos, workspace_id=bootstrap["workspace_id"], close_target=True
    )
    assert forced["stopped_reason"] == "TARGET_CLOSED", forced
    closed = repos.exceptions.get(case.id)
    assert closed is not None and closed.status == str(ExceptionStatus.CLOSED)
    assert closed.closed_at is not None


def test_run_tick_is_deterministic_for_clock_offset(db_session, bootstrap):
    repos = _support.repos_for(db_session, bootstrap)
    first = tick.run_tick(db_session, repos, workspace_id=bootstrap["workspace_id"], minutes=60)
    assert first["minutes"] == 60
    assert first["offset_minutes"] == clock_state.offset_minutes == 60
    second = tick.run_tick(db_session, repos, workspace_id=bootstrap["workspace_id"], minutes=30)
    assert second["offset_minutes"] == 90
