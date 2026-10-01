"""AI 受限层三个入口（契约冻结，backend-domain 按此调用）。

    execute_analysis(session, repos, analysis_id) -> {"status", "output", "risk_level"}
    parse_message(session, repos, message_id)     -> {"status", "output", "model", ...}
    draft_notice(session, repos, exception_id, analysis_id=None) -> {"subject", "content", "tone"}

事务边界：每个入口在自身结束时 `commit()`（一次 AI 任务 = 一个事务），
失败时先落库 FAILED（含 error_code / raw_output）再抛可识别异常：
- `AiUnavailable`  → LLM_UNAVAILABLE（超时 / 无 Key / 额度超限）
- `AiOutputInvalid`→ AI_OUTPUT_INVALID（schema 非法或与系统事实不一致，已拦截）
调用方（backend-domain）据此把异常状态从 ANALYZING 回退到 CONFIRMING。
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from app.ai import contexts
from app.ai import facts as F
from app.ai.agent import (
    MAX_LOOP_STEPS,
    TOTAL_TIMEOUT_SECONDS,
    AgentState,
    StepRecorder,
    run_agent,
    t1_validator,
    t2_validator,
    t3_validator,
)
from app.ai.errors import AiOutputInvalid, AiUnavailable
from app.ai.prompts import DEFAULT_PROMPT_VERSION, render_prompt, system_prompt
from app.ai.providers import LiveProvider, Provider, observation_text
from app.ai.replay import ReplayProvider, ReplayStore
from app.core.clock import now_utc, utcnow_naive
from app.core.config import Settings, get_settings
from app.core.errors import ErrorCode, not_found
from app.services import read_models

T2_DEFAULT_PROMPT_VERSION = DEFAULT_PROMPT_VERSION
TASK_PARSE = "PARSE_MESSAGE"
TASK_ANALYZE = "ANALYZE_EXCEPTION"
TASK_NOTICE = "DRAFT_NOTICE"


# --- 幂等键（§11.2 表 1） -----------------------------------------------------
def _stable_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def compute_t2_input_hash(facts: dict[str, Any]) -> str:
    """sha256(异常关键字段 + 最新轨迹 id + 消息 id)。"""
    case = F.case_of(facts)
    events = facts.get("tracking_events") or []
    message = F.latest_message_of(facts)
    payload = {
        "exception_id": case.get("id"),
        "case_no": case.get("case_no"),
        "status": case.get("status"),
        "type": case.get("type"),
        "expected_eta_at": case.get("expected_eta_at"),
        "promised_delivery_at": case.get("promised_delivery_at"),
        "sla_delay_minutes": F.backend_delay_minutes(facts),
        "sla_breached": F.backend_sla_breached(facts),
        "latest_tracking_event_id": (events[0] or {}).get("id") if events else None,
        "latest_message_id": message.get("id"),
    }
    return _sha256(_stable_json(payload))


def compute_t1_input_hash(raw_text: str, status: str | None = None) -> str:
    """sha256(raw_text + 当前状态)。"""
    return _sha256(f"{raw_text}|{status or ''}")


def compute_t3_input_hash(analysis_id: int | None, facts: dict[str, Any]) -> str:
    """sha256(T2 输出 id + 事实快照)。"""
    case = F.case_of(facts)
    snapshot = {
        "analysis_id": analysis_id,
        "exception_id": case.get("id"),
        "expected_eta_at": case.get("expected_eta_at"),
        "promised_delivery_at": case.get("promised_delivery_at"),
        "delay_minutes": F.backend_delay_minutes(facts),
        "sla_breached": F.backend_sla_breached(facts),
    }
    return _sha256(_stable_json(snapshot))


# --- Provider 选择 ------------------------------------------------------------
def build_provider(
    *,
    provider: Provider | None = None,
    replay_dir: str | None = None,
    settings: Settings | None = None,
) -> Provider:
    """默认 AI_MODE=replay → ReplayProvider；AI_MODE=live → LiveProvider。"""
    if provider is not None:
        return provider
    settings = settings or get_settings()
    if not settings.ai_replay_enabled:
        return LiveProvider()
    return ReplayProvider(ReplayStore(replay_dir))


# --- 事务与降级落库 -----------------------------------------------------------
def _commit(session: Any) -> None:
    try:
        session.commit()
    except Exception:  # pragma: no cover - 交由调用方/上层处理
        session.rollback()
        raise


def _fail(session: Any, analysis: Any, exc: Exception, *, latency_ms: int) -> None:
    code = getattr(exc, "code", None) or ErrorCode.INTERNAL_ERROR
    details = getattr(exc, "details", {}) or {}
    errors = details.get("errors")
    analysis.status = "FAILED"
    analysis.error_code = str(code)
    if errors:
        analysis.error_message = "; ".join(str(item) for item in errors)[:255]
    else:
        analysis.error_message = str(exc)[:255]
    analysis.raw_output = (details.get("raw_output") or analysis.raw_output or None) or None
    analysis.finished_at = utcnow_naive()
    analysis.latency_ms = latency_ms
    session.flush()
    _commit(session)


# --- T2 入口 -----------------------------------------------------------------
def execute_analysis(
    session: Any,
    repos: Any,
    analysis_id: int,
    *,
    provider: Provider | None = None,
    replay_dir: str | None = None,
    max_steps: int = MAX_LOOP_STEPS,
    timeout_seconds: float = TOTAL_TIMEOUT_SECONDS,
    monotonic: Any = time.monotonic,
) -> dict[str, Any]:
    analysis = repos.analyses.get(analysis_id)
    if analysis is None:
        raise not_found(f"分析任务 {analysis_id} 不存在")

    started = monotonic()
    case = repos.exceptions.get(analysis.exception_id)
    if case is None:
        _fail(session, analysis, not_found(f"异常 {analysis.exception_id} 不存在"), latency_ms=0)
        raise not_found(f"异常 {analysis.exception_id} 不存在")

    facts = read_models.exception_facts(repos, case.id)
    input_hash = analysis.input_hash or compute_t2_input_hash(facts)

    analysis.status = "RUNNING"
    analysis.started_at = utcnow_naive()
    analysis.prompt_version = T2_DEFAULT_PROMPT_VERSION
    analysis.input_hash = input_hash
    session.flush()

    chosen = build_provider(provider=provider, replay_dir=replay_dir)
    state = AgentState(
        task_type=TASK_ANALYZE,
        facts=facts,
        repos=repos,
        context={"exception_id": case.id, "analysis_id": analysis.id},
        input_hash=input_hash,
        system=system_prompt(TASK_ANALYZE),
    )
    state.evidence.add_facts(facts)
    state.prompt = _t2_prompt(state)

    recorder = StepRecorder(repos, analysis.id)
    try:
        result = run_agent(
            provider=chosen,
            state=state,
            validate=t2_validator,
            recorder=recorder,
            prompt_version=T2_DEFAULT_PROMPT_VERSION,
            max_steps=max_steps,
            timeout_seconds=timeout_seconds,
            refresh_prompt=_t2_prompt,
            monotonic=monotonic,
        )
    except Exception as exc:  # noqa: BLE001 - 统一落库 FAILED 后再抛
        _fail(session, analysis, exc, latency_ms=int((monotonic() - started) * 1000))
        raise

    risk_level = F.backend_risk_level(facts)
    analysis.output_json = result.output
    analysis.raw_output = result.raw_output
    analysis.status = "READY"
    analysis.model = result.model
    analysis.prompt_version = result.prompt_version
    analysis.tokens_in = result.tokens_in
    analysis.tokens_out = result.tokens_out
    analysis.latency_ms = result.latency_ms
    analysis.is_replay = result.is_replay
    analysis.risk_level_calculated = risk_level
    analysis.error_code = None
    analysis.error_message = None
    analysis.finished_at = utcnow_naive()
    session.flush()
    _commit(session)
    return {"status": analysis.status, "output": result.output, "risk_level": risk_level}


# --- T1 入口 -----------------------------------------------------------------
def parse_message(
    session: Any,
    repos: Any,
    message_id: int,
    *,
    provider: Provider | None = None,
    replay_dir: str | None = None,
    max_steps: int = MAX_LOOP_STEPS,
    timeout_seconds: float = TOTAL_TIMEOUT_SECONDS,
    monotonic: Any = time.monotonic,
) -> dict[str, Any]:
    message = repos.messages.get(message_id)
    if message is None:
        raise not_found(f"承运商消息 {message_id} 不存在")

    case = repos.exceptions.get(message.exception_id)
    facts = read_models.exception_facts(repos, message.exception_id) if case is not None else {}
    order = F.order_of(facts)
    vehicle = F.vehicle_of(facts)
    input_hash = compute_t1_input_hash(message.raw_text, message.parse_status)

    state = AgentState(
        task_type=TASK_PARSE,
        facts=facts,
        repos=repos,
        context={
            "exception_id": message.exception_id,
            "message_id": message.id,
            "raw_text": message.raw_text,
            "now": read_models.iso(now_utc()),
            "occurred_at": F.case_of(facts).get("occurred_at"),
            "expected_eta_at": F.case_of(facts).get("expected_eta_at"),
            "raw_now": now_utc(),
        },
        input_hash=input_hash,
        system=system_prompt(TASK_PARSE),
    )
    state.evidence.add_facts(facts)
    state.prompt = _t1_prompt(state, order=order, vehicle=vehicle)

    chosen = build_provider(provider=provider, replay_dir=replay_dir)
    try:
        result = run_agent(
            provider=chosen,
            state=state,
            validate=t1_validator,
            recorder=None,
            prompt_version=DEFAULT_PROMPT_VERSION,
            max_steps=max_steps,
            timeout_seconds=timeout_seconds,
            monotonic=monotonic,
        )
    except Exception as exc:  # noqa: BLE001
        message.parse_status = "FAILED"
        message.parser_version = DEFAULT_PROMPT_VERSION
        message.parse_error = str(exc)[:255]
        session.flush()
        _commit(session)
        raise

    output = dict(result.output)
    message.parse_result_json = {
        **output,
        "meta": {
            "model": result.model,
            "is_replay": result.is_replay,
            "prompt_version": result.prompt_version,
            "missing_info": output.get("missing_info") or [],
        },
    }
    message.parse_status = "PARSED"
    message.parser_version = result.prompt_version
    message.parse_error = None
    session.flush()
    _commit(session)
    return {
        "status": message.parse_status,
        "output": output,
        "model": result.model,
        "is_replay": result.is_replay,
        "prompt_version": result.prompt_version,
    }


# --- T3 入口 -----------------------------------------------------------------
def draft_notice(
    session: Any,
    repos: Any,
    exception_id: int,
    analysis_id: int | None = None,
    *,
    provider: Provider | None = None,
    replay_dir: str | None = None,
    max_steps: int = MAX_LOOP_STEPS,
    timeout_seconds: float = TOTAL_TIMEOUT_SECONDS,
    monotonic: Any = time.monotonic,
) -> dict[str, Any]:
    del session  # 草稿不写库：仅返回结构，由 backend-domain 走 approval → NotificationService
    case = repos.exceptions.get(exception_id)
    if case is None:
        raise not_found(f"异常 {exception_id} 不存在")

    facts = read_models.exception_facts(repos, exception_id)
    analysis = repos.analyses.get(analysis_id) if analysis_id else repos.analyses.latest_for_case(exception_id)
    analysis_output: dict[str, Any] = {}
    resolved_analysis_id: int | None = None
    if analysis is not None and analysis.status == "READY" and isinstance(analysis.output_json, dict):
        analysis_output = analysis.output_json
        resolved_analysis_id = analysis.id
    input_hash = compute_t3_input_hash(resolved_analysis_id, facts)

    state = AgentState(
        task_type=TASK_NOTICE,
        facts=facts,
        repos=repos,
        context={"exception_id": exception_id, "analysis": analysis_output, "analysis_id": resolved_analysis_id},
        input_hash=input_hash,
        system=system_prompt(TASK_NOTICE),
    )
    state.evidence.add_facts(facts)
    state.prompt = _t3_prompt(state, analysis_output)

    chosen = build_provider(provider=provider, replay_dir=replay_dir)
    result = run_agent(
        provider=chosen,
        state=state,
        validate=t3_validator,
        recorder=None,
        prompt_version=DEFAULT_PROMPT_VERSION,
        max_steps=max_steps,
        timeout_seconds=timeout_seconds,
        monotonic=monotonic,
    )
    return {
        "subject": result.output["subject"],
        "content": result.output["content"],
        "tone": result.output["tone"],
    }


# --- Prompt 组装（refresh 时会在工具执行后重建，live 模式能看到工具观测） ------
def _t2_prompt(state: AgentState) -> str:
    knowledge: list[dict[str, Any]] = []
    outcome = state.tool_results.get("search_knowledge")
    if outcome is not None and isinstance(outcome.payload, list):
        knowledge = [chunk for chunk in outcome.payload if isinstance(chunk, dict)]
    payload = contexts.build_t2_context(
        facts=state.facts,
        knowledge=knowledge,
        tool_observations=observation_text(state) if state.tool_results else "",
    )
    text, _ = render_prompt("t2_analyze_exception", payload)
    return text


def _t1_prompt(state: AgentState, *, order: dict[str, Any], vehicle: dict[str, Any]) -> str:
    payload = contexts.build_t1_context(
        raw_text=str(state.context.get("raw_text") or ""),
        now=state.context.get("now"),
        order_no=order.get("order_no"),
        origin_city=order.get("origin_city"),
        dest_city=order.get("dest_city"),
        plate_no=vehicle.get("plate_no"),
        vehicle_status=vehicle.get("status"),
        expected_eta_at=state.context.get("expected_eta_at"),
        occurred_at=state.context.get("occurred_at"),
    )
    text, _ = render_prompt("t1_parse_message", payload)
    return text


def _t3_prompt(state: AgentState, analysis_output: dict[str, Any]) -> str:
    payload = contexts.build_t3_context(facts=state.facts, analysis=analysis_output)
    text, _ = render_prompt("t3_draft_notice", payload)
    return text


__all__ = [
    "TASK_ANALYZE",
    "TASK_NOTICE",
    "TASK_PARSE",
    "AiOutputInvalid",
    "AiUnavailable",
    "build_provider",
    "compute_t1_input_hash",
    "compute_t2_input_hash",
    "compute_t3_input_hash",
    "draft_notice",
    "execute_analysis",
    "parse_message",
]
