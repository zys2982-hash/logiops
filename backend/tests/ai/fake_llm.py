"""FakeLLM：脚本化 Provider（不联网、不依赖 API Key）。

用法：
    provider = ScriptedProvider([final_result(output)])
    provider = ScriptedProvider([tool_turn([ToolCall("get_order", {...})]), final_result(output)])
    provider = ScriptedProvider([AiUnavailable("超时")])
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.ai.providers import ProviderResult, ToolCall

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "ai"


def load_fixture(name: str) -> dict[str, Any]:
    path = FIXTURE_DIR / name
    return json.loads(path.read_text(encoding="utf-8"))


def final_result(
    output: dict[str, Any] | None,
    *,
    model: str = "fake-llm",
    raw_text: str | None = None,
    is_replay: bool = False,
    tokens_in: int = 120,
    tokens_out: int = 80,
) -> ProviderResult:
    if raw_text is None and output is not None:
        raw_text = json.dumps(output, ensure_ascii=False)
    return ProviderResult(
        final=output,
        raw_text=raw_text,
        model=model,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        is_replay=is_replay,
    )


def tool_turn(calls: list[ToolCall], *, model: str = "fake-llm") -> ProviderResult:
    return ProviderResult(tool_calls=calls, raw_text=None, model=model, tokens_in=50, tokens_out=30)


def fixture_result(name: str, *, model: str = "replay") -> ProviderResult:
    payload = load_fixture(name)
    output = payload.get("output")
    return ProviderResult(
        final=output,
        raw_text=json.dumps(output, ensure_ascii=False),
        model=model,
        tokens_in=int(payload.get("tokens_in") or 0),
        tokens_out=int(payload.get("tokens_out") or 0),
        is_replay=True,
    )


class ScriptedProvider:
    """按脚本返回回合；脚本元素可以是 ProviderResult 或待抛出的异常。"""

    is_replay = True

    def __init__(
        self,
        results: list[Any],
        *,
        preset: list[ToolCall] | None = None,
        model_name: str = "fake-llm",
    ) -> None:
        self._queue = list(results)
        self._preset = list(preset) if preset else None
        self.model_name = model_name
        self.calls: list[dict[str, Any]] = []

    def preset_plan(self, task_type: str, *, state: Any) -> list[ToolCall] | None:
        return list(self._preset) if self._preset else None

    def next_step(self, task_type: str, *, state: Any, repair_errors: list[str]) -> ProviderResult:
        self.calls.append(
            {
                "task_type": task_type,
                "repair_errors": list(repair_errors),
                "tools_done": [call.name for call in state.tool_calls],
                "prompt_len": len(state.prompt or ""),
                "prompt": state.prompt or "",
            }
        )
        if not self._queue:
            raise AssertionError("ScriptedProvider 队列已空（脚本回合数不足）")
        item = self._queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


__all__ = [
    "FIXTURE_DIR",
    "ScriptedProvider",
    "fixture_result",
    "final_result",
    "load_fixture",
    "tool_turn",
]
