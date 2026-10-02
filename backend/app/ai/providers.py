"""Provider 抽象：replay（fixture + 模板回退）与 live（真实 LLM）共用同一有界循环。

约定：
- `preset_plan()` 仅 replay 有值（录制好的工具序列 / 默认事实计划），live 返回 None；
- `next_step()` 每次调用代表一次"模型回合"：要么请求工具（tool_calls），
  要么给出最终 JSON（final）；agent 负责写 step、超时与预算。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from app.ai.llm import LLMClient
from app.ai.tools import tool_schema_for_llm
from app.core.config import get_settings

if TYPE_CHECKING:  # pragma: no cover - 仅类型
    from app.ai.agent import AgentState

_CODE_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


@dataclass
class ToolCall:
    name: str
    args: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "args": self.args}


@dataclass
class ProviderResult:
    tool_calls: list[ToolCall] = field(default_factory=list)
    final: dict[str, Any] | None = None
    raw_text: str | None = None
    model: str = "unknown"
    tokens_in: int = 0
    tokens_out: int = 0
    is_replay: bool = False


class Provider(Protocol):
    is_replay: bool
    model_name: str

    def preset_plan(self, task_type: str, *, state: AgentState) -> list[ToolCall] | None: ...

    def next_step(
        self,
        task_type: str,
        *,
        state: AgentState,
        repair_errors: list[str],
    ) -> ProviderResult: ...


def parse_json_object(text: str | None) -> dict[str, Any] | None:
    """从模型文本里抽第一个 JSON 对象（容忍 markdown 代码块与前后缀）。"""
    if not text:
        return None
    candidates: list[str] = []
    fenced = _CODE_FENCE_RE.search(text)
    if fenced:
        candidates.append(fenced.group(1))
    candidates.append(text)
    for candidate in candidates:
        candidate = candidate.strip()
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start == -1 or end <= start:
            continue
        try:
            value = json.loads(candidate[start : end + 1])
        except (ValueError, TypeError):
            continue
        if isinstance(value, dict):
            return value
    return None


def _looks_complete_json(text: str | None) -> bool:
    """粗判模型文本是否是"看起来完整"的 JSON（括号配平），用于识别被截断的输出。"""
    if not text:
        return False
    stripped = text.strip()
    if not (stripped.startswith("{") and stripped.endswith("}")):
        return False
    return stripped.count("{") == stripped.count("}") and stripped.count("[") == stripped.count("]")


def tool_calls_from_payload(payload: dict[str, Any]) -> list[ToolCall]:
    """兼容 {"tool_call": {...}} 与 {"tool_calls": [...]} 两种模型输出形态。"""
    raw: Any = payload.get("tool_calls")
    if raw is None and isinstance(payload.get("tool_call"), dict):
        raw = [payload["tool_call"]]
    if not isinstance(raw, list):
        return []
    calls: list[ToolCall] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = item.get("name") or (item.get("function") or {}).get("name")
        if not name:
            continue
        args = item.get("args") or item.get("arguments") or (item.get("function") or {}).get("arguments") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except (ValueError, TypeError):
                args = {}
        calls.append(ToolCall(name=str(name), args=args if isinstance(args, dict) else {}))
    return calls


def observation_text(state: AgentState) -> str:
    """把已执行工具的结果摘要拼成可回灌给模型/进日志的文本。"""
    if not state.tool_results:
        return "（尚未调用工具）"
    lines: list[str] = []
    for outcome in state.tool_results.values():
        payload = outcome.payload
        if isinstance(payload, (dict, list)):
            body = json.dumps(payload, ensure_ascii=False, default=str)
        else:
            body = str(payload)
        lines.append(
            f"- {outcome.name}({json.dumps(outcome.args, ensure_ascii=False)}) → {outcome.summary}"
            f" | {body[:600]}"
        )
    return "\n".join(lines)


class LiveProvider:
    """AI_MODE=live：真实 LLM + tool-calling。"""

    is_replay = False

    def __init__(self, client: LLMClient | None = None, *, temperature: float | None = None) -> None:
        self.client = client or LLMClient()
        self.temperature = temperature
        self.model_name = get_settings().llm_model

    def preset_plan(self, task_type: str, *, state: AgentState) -> list[ToolCall] | None:
        return None

    def next_step(
        self,
        task_type: str,
        *,
        state: AgentState,
        repair_errors: list[str],
    ) -> ProviderResult:
        user = self._build_user(state, repair_errors)
        response = self.client.complete(
            task_type=task_type,
            system=state.system,
            user=user,
            temperature=self.temperature,
            tools=tool_schema_for_llm() if task_type == "ANALYZE_EXCEPTION" else None,
        )
        payload = parse_json_object(response.text)
        calls = tool_calls_from_payload(payload or {})
        if not payload and not calls and not _looks_complete_json(response.text):
            # 输出被 max_tokens 截断的典型特征：JSON 未闭合。给出可执行的修复提示，
            # 否则下一轮模型只会重复同样超长的输出。
            state.context["truncated_hint"] = (
                "上一轮输出**被截断**（JSON 未闭合，通常因为篇幅过长）：请大幅压缩 summary 与 rationale，"
                "只保留 1–3 条建议与 3–5 条证据引用，确保输出是一个完整闭合的 JSON 对象。"
            )
        else:
            state.context.pop("truncated_hint", None)
        if calls:
            return ProviderResult(
                tool_calls=calls,
                raw_text=response.text,
                model=response.model,
                tokens_in=response.tokens_in,
                tokens_out=response.tokens_out,
                is_replay=False,
            )
        return ProviderResult(
            final=payload,
            raw_text=response.text,
            model=response.model,
            tokens_in=response.tokens_in,
            tokens_out=response.tokens_out,
            is_replay=False,
        )

    def _build_user(self, state: AgentState, repair_errors: list[str]) -> str:
        sections = [state.prompt]
        sections.append("## 已执行的只读工具结果\n" + observation_text(state))
        if state.context.get("force_final"):
            sections.append(
                "## 步数即将用尽\n"
                "请**立刻**只输出最终 JSON 对象，不要再调用任何工具。"
                "已有信息足以完成分析；缺失的信息写进 open_questions。"
            )
        if state.context.get("truncated_hint"):
            sections.append("## 输出被截断\n" + str(state.context["truncated_hint"]))
        if repair_errors:
            sections.append(
                "## 上一轮输出未通过校验，请修正后重新输出完整 JSON\n"
                + "\n".join(f"- {item}" for item in repair_errors)
            )
        sections.append("请只输出一个 JSON 对象，不要额外文字。")
        return "\n\n".join(sections)


__all__ = [
    "LiveProvider",
    "Provider",
    "ProviderResult",
    "ToolCall",
    "observation_text",
    "parse_json_object",
    "tool_calls_from_payload",
]
