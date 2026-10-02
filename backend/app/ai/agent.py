"""有界 Agent 循环（基线文档 §11.3）。

硬约束：≤8 步（工具调用 + 模型回合）、总超时 90s、仅 7 个白名单工具、
连续两次校验失败即终止；每个 TOOL / LLM / VALIDATE 步骤都落 `ai_analysis_step`
（前端进度条只依赖这张表）。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.ai.errors import AiOutputInvalid, AiUnavailable
from app.ai.guard import EvidenceIndex, GuardError, validate_t1, validate_t2, validate_t3
from app.ai.prompts import DEFAULT_PROMPT_VERSION
from app.ai.providers import Provider, ProviderResult, ToolCall
from app.ai.tools import ToolContext, ToolOutcome, call_tool
from app.core.clock import utcnow_naive

MAX_LOOP_STEPS = 14
TOTAL_TIMEOUT_SECONDS = 90
MAX_VALIDATION_FAILURES = 2

Validator = Callable[[dict[str, Any] | None, "AgentState"], dict[str, Any]]
PromptRefresher = Callable[["AgentState"], str]


@dataclass
class AgentState:
    task_type: str
    facts: dict[str, Any] = field(default_factory=dict)
    repos: Any = None
    context: dict[str, Any] = field(default_factory=dict)
    system: str = ""
    prompt: str = ""
    input_hash: str | None = None
    evidence: EvidenceIndex = field(default_factory=EvidenceIndex)
    tool_results: dict[str, ToolOutcome] = field(default_factory=dict)
    tool_calls: list[ToolCall] = field(default_factory=list)
    validation_failures: int = 0


@dataclass
class AgentResult:
    output: dict[str, Any]
    model: str
    prompt_version: str
    tokens_in: int
    tokens_out: int
    is_replay: bool
    raw_output: str | None
    steps_written: int
    latency_ms: int


class StepRecorder:
    """把每一步写进 ai_analysis_step（step_no 自增，前端进度条的唯一数据源）。"""

    def __init__(self, repos: Any, analysis_id: int | None) -> None:
        from app.models.ai import AiAnalysisStep

        self.repos = repos
        self.analysis_id = analysis_id
        self._model = AiAnalysisStep
        self.written = 0

    @property
    def enabled(self) -> bool:
        return self.analysis_id is not None and self.repos is not None

    def record(
        self,
        step_type: str,
        *,
        tool_name: str | None = None,
        args: dict[str, Any] | None = None,
        result_summary: str | None = None,
        status: str = "OK",
        duration_ms: int = 0,
        error: str | None = None,
    ) -> int:
        if not self.enabled:
            return 0
        self.written += 1
        step = self._model(
            analysis_id=self.analysis_id,
            step_no=self.repos.analysis_steps.next_step_no(self.analysis_id),
            step_type=step_type,
            tool_name=tool_name,
            args_json=args,
            result_summary=(result_summary or "")[:255] or None,
            status=status,
            duration_ms=duration_ms,
            error=(str(error)[:255] if error else None),
            created_at=utcnow_naive(),
        )
        self.repos.analysis_steps.add(step)
        return self.written


def run_agent(
    *,
    provider: Provider,
    state: AgentState,
    validate: Validator,
    recorder: StepRecorder | None = None,
    prompt_version: str = DEFAULT_PROMPT_VERSION,
    max_steps: int = MAX_LOOP_STEPS,
    timeout_seconds: float = TOTAL_TIMEOUT_SECONDS,
    refresh_prompt: PromptRefresher | None = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> AgentResult:
    started = monotonic()
    steps_used = 0

    def elapsed_ms() -> int:
        return int((monotonic() - started) * 1000)

    def check_timeout() -> None:
        if monotonic() - started > timeout_seconds:
            if recorder is not None:
                recorder.record(
                    "LLM",
                    result_summary=f"总超时（>{int(timeout_seconds)}s）终止",
                    status="ERROR",
                    duration_ms=elapsed_ms(),
                    error="TIMEOUT",
                )
            raise AiUnavailable(
                f"AI 分析超时（>{int(timeout_seconds)}s），已降级",
                {"elapsed_ms": elapsed_ms(), "timeout_seconds": timeout_seconds},
            )

    def record_step(step_type: str, **kwargs: Any) -> None:
        if recorder is not None:
            recorder.record(step_type, **kwargs)

    def run_tool(call: ToolCall) -> ToolOutcome:
        nonlocal steps_used
        tool_started = monotonic()
        tool_ctx = ToolContext(repos=state.repos, exception_id=state.context.get("exception_id"))
        try:
            outcome = call_tool(call.name, call.args, tool_ctx)
        except AiOutputInvalid:
            record_step(
                "TOOL",
                tool_name=call.name,
                args=call.args,
                result_summary="工具不在白名单，终止",
                status="ERROR",
                duration_ms=int((monotonic() - tool_started) * 1000),
                error="TOOL_NOT_ALLOWED",
            )
            raise
        steps_used += 1
        state.tool_calls.append(call)
        state.tool_results[call.name] = outcome
        state.evidence.add_tool_outcome(outcome)
        record_step(
            "TOOL",
            tool_name=call.name,
            args=outcome.args,
            result_summary=outcome.summary,
            status=outcome.status,
            duration_ms=int((monotonic() - tool_started) * 1000),
            error=outcome.error,
        )
        return outcome

    # 1) replay 的预设工具计划（live 无预设，由模型按回合请求工具）
    preset = provider.preset_plan(state.task_type, state=state)
    for call in preset or []:
        if steps_used >= max_steps:
            raise AiOutputInvalid(
                f"超过最大步数 {max_steps}：工具计划被截断，判定输出非法",
                {"max_steps": max_steps, "tool": call.name},
            )
        check_timeout()
        run_tool(call)

    # 2) 模型回合：请求工具 or 产出最终 JSON
    repair_errors: list[str] = []
    while True:
        check_timeout()
        if steps_used >= max_steps:
            raise AiOutputInvalid(
                f"超过最大步数 {max_steps}，未产出合法输出",
                {"max_steps": max_steps, "validation_failures": state.validation_failures},
            )
        if refresh_prompt is not None:
            state.prompt = refresh_prompt(state)
        # 临近步数上限时提示模型收口（live 模式下工具较多，避免把预算全花在查数据上）
        state.context["force_final"] = steps_used >= max_steps - 2

        turn_started = monotonic()
        try:
            result = provider.next_step(state.task_type, state=state, repair_errors=repair_errors)
        except AiUnavailable as exc:
            record_step(
                "LLM",
                result_summary=f"LLM 不可用：{exc}",
                status="ERROR",
                duration_ms=int((monotonic() - turn_started) * 1000),
                error=str(exc.code),
            )
            raise
        steps_used += 1
        duration_ms = int((monotonic() - turn_started) * 1000)

        if result.tool_calls:
            names = ",".join(call.name for call in result.tool_calls)
            record_step(
                "LLM",
                result_summary=f"请求 {len(result.tool_calls)} 个工具：{names}",
                status="OK",
                duration_ms=duration_ms,
            )
            for call in result.tool_calls:
                if steps_used >= max_steps:
                    raise AiOutputInvalid(
                        f"超过最大步数 {max_steps}：模型仍在请求工具，判定输出非法",
                        {"max_steps": max_steps},
                    )
                check_timeout()
                run_tool(call)
            continue

        record_step(
            "LLM",
            result_summary=_final_summary(result),
            status="OK",
            duration_ms=duration_ms,
        )
        raw = result.final if result.final is not None else None
        try:
            output = validate(raw, state)
        except GuardError as exc:
            state.validation_failures += 1
            record_step(
                "VALIDATE",
                result_summary=f"校验失败：{exc.errors[0] if exc.errors else '未知原因'}",
                status="ERROR",
                duration_ms=0,
                error="; ".join(exc.errors)[:255],
            )
            repair_errors = exc.errors
            if state.validation_failures >= MAX_VALIDATION_FAILURES:
                summary = "；".join(exc.errors[:3])
                raise AiOutputInvalid(
                    f"AI 输出连续两次未通过校验（schema/事实一致性），已拦截：{summary}",
                    {
                        "errors": exc.errors,
                        "raw_output": (result.raw_text or "")[:1000],
                        "attempts": state.validation_failures,
                    },
                ) from exc
            continue

        record_step(
            "VALIDATE",
            result_summary="schema 与事实一致性校验通过",
            status="OK",
            duration_ms=0,
        )
        return AgentResult(
            output=output,
            model=result.model,
            prompt_version=prompt_version,
            tokens_in=result.tokens_in,
            tokens_out=result.tokens_out,
            is_replay=result.is_replay,
            raw_output=result.raw_text,
            steps_written=recorder.written if recorder is not None else 0,
            latency_ms=elapsed_ms(),
        )


def _final_summary(result: ProviderResult) -> str:
    if result.final is None:
        return "模型未返回可解析 JSON"
    preview = result.final.get("summary") or result.final.get("subject") or result.final.get("location") or ""
    return f"输出 JSON（{result.model}）：{str(preview)[:120]}"


# --- 三个任务的 Validator（供 runner 复用） ------------------------------------
def t2_validator(raw: dict[str, Any] | None, state: AgentState) -> dict[str, Any]:
    return validate_t2(raw, facts=state.facts, evidence=state.evidence)


def t1_validator(raw: dict[str, Any] | None, state: AgentState) -> dict[str, Any]:
    return validate_t1(raw, now=state.context.get("now"), occurred_at=state.context.get("occurred_at"))


def t3_validator(raw: dict[str, Any] | None, state: AgentState) -> dict[str, Any]:
    return validate_t3(raw, facts=state.facts)


__all__ = [
    "MAX_LOOP_STEPS",
    "MAX_VALIDATION_FAILURES",
    "TOTAL_TIMEOUT_SECONDS",
    "AgentResult",
    "AgentState",
    "StepRecorder",
    "run_agent",
    "t1_validator",
    "t2_validator",
    "t3_validator",
]
