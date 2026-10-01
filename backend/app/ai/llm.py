"""OpenAI 兼容 LLM 客户端（基线文档 §11.7）。

全部参数来自 settings：base_url / model / api_key / timeout / token 上限 / 每日成本上限。
- 重试：`llm_max_retries`（默认 2），退避 1s / 3s
- 超时：`llm_timeout_seconds`（默认 60s，循环总超时 90s 由 agent 控制）
- 成本：进程内每日台账，超限直接 AiUnavailable（§11.8「今日 AI 额度已用完」）
- 失败一律抛 `AiUnavailable`（可识别，调用方据此降级 FAILED）

默认 AI_MODE=replay，本模块不会被走到；live 模式才需要 API Key。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from app.ai.errors import AiUnavailable
from app.core.clock import now_utc
from app.core.config import Settings, get_settings

BACKOFF_SECONDS: tuple[float, ...] = (1.0, 3.0)
# 演示口径的单价（人民币/1k tokens）；换模型只改这里或环境变量即可
PRICE_CNY_PER_1K: dict[str, tuple[float, float]] = {
    "default": (0.001, 0.002),
}


def estimate_tokens(text: str | None) -> int:
    """粗估 token：中文约 1 字 ≈ 1 token，这里取 len/2 的保守下界（够用于上限保护）。"""
    if not text:
        return 0
    return max(1, len(text) // 2)


def estimate_cost_cny(model: str, tokens_in: int, tokens_out: int) -> float:
    price_in, price_out = PRICE_CNY_PER_1K.get(model, PRICE_CNY_PER_1K["default"])
    return round(tokens_in / 1000 * price_in + tokens_out / 1000 * price_out, 6)


@dataclass
class LLMResponse:
    text: str
    model: str
    tokens_in: int
    tokens_out: int
    latency_ms: int
    raw: dict[str, Any] | None = None


class DailyCostLedger:
    """进程内每日花费台账（演示规模足够；重启即清零）。"""

    def __init__(self, limit_cny: float) -> None:
        self.limit_cny = float(limit_cny)
        self._day: str | None = None
        self._spent: float = 0.0

    def _roll(self) -> None:
        today = now_utc().strftime("%Y-%m-%d")
        if self._day != today:
            self._day = today
            self._spent = 0.0

    @property
    def spent_cny(self) -> float:
        self._roll()
        return self._spent

    @property
    def remaining_cny(self) -> float:
        return max(0.0, self.limit_cny - self.spent_cny)

    def ensure_budget(self, estimated_cny: float) -> None:
        self._roll()
        if self._spent + estimated_cny > self.limit_cny:
            raise AiUnavailable(
                f"今日 AI 额度已用完（已用 ¥{self._spent:.4f} / 上限 ¥{self.limit_cny:.2f}）",
                {"spent_cny": round(self._spent, 6), "limit_cny": self.limit_cny},
            )

    def record(self, cost_cny: float) -> None:
        self._roll()
        self._spent += float(cost_cny)


_SHARED_LEDGER: DailyCostLedger | None = None


def shared_ledger() -> DailyCostLedger:
    global _SHARED_LEDGER
    if _SHARED_LEDGER is None or _SHARED_LEDGER.limit_cny != float(get_settings().llm_daily_cost_limit_cny):
        _SHARED_LEDGER = DailyCostLedger(get_settings().llm_daily_cost_limit_cny)
    return _SHARED_LEDGER


def reset_shared_ledger() -> None:
    """测试用：清空每日台账。"""
    global _SHARED_LEDGER
    _SHARED_LEDGER = None


class LLMClient:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        ledger: DailyCostLedger | None = None,
        sleep: Any = time.sleep,
        max_retries: int | None = None,
        backoff_seconds: tuple[float, ...] = BACKOFF_SECONDS,
    ) -> None:
        self.settings = settings or get_settings()
        self.ledger = ledger if ledger is not None else shared_ledger()
        self._sleep = sleep
        self.max_retries = self.settings.llm_max_retries if max_retries is None else max_retries
        self.backoff_seconds = backoff_seconds

    # --- 对外 ---
    def complete(
        self,
        *,
        task_type: str,
        system: str,
        user: str,
        temperature: float | None = None,
        max_output_tokens: int | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        settings = self.settings
        if not settings.llm_api_key:
            raise AiUnavailable("未配置 LLM_API_KEY，无法调用真实模型（请使用 AI_MODE=replay）")

        estimated_in = estimate_tokens(system) + estimate_tokens(user)
        if estimated_in > settings.llm_max_input_tokens:
            raise AiUnavailable(
                f"输入超过 token 上限（估 {estimated_in} > {settings.llm_max_input_tokens}）",
                {"estimated_tokens_in": estimated_in},
            )

        output_cap = min(max_output_tokens or settings.llm_max_output_tokens, settings.llm_max_output_tokens)
        self.ledger.ensure_budget(estimate_cost_cny(settings.llm_model, estimated_in, output_cap))

        attempts = 1 + max(0, self.max_retries)
        last_error: Exception | None = None
        for attempt in range(attempts):
            started = time.monotonic()
            try:
                response = self._call_once(
                    system=system,
                    user=user,
                    temperature=temperature,
                    max_output_tokens=output_cap,
                    tools=tools,
                )
                latency_ms = int((time.monotonic() - started) * 1000)
                self.ledger.record(
                    estimate_cost_cny(response.model, response.tokens_in, response.tokens_out)
                )
                response.latency_ms = latency_ms
                return response
            except AiUnavailable:
                raise
            except Exception as exc:  # noqa: BLE001 - 任何 SDK 异常都降级为 AiUnavailable
                last_error = exc
                if attempt < attempts - 1:
                    delay = self.backoff_seconds[min(attempt, len(self.backoff_seconds) - 1)]
                    self._sleep(delay)
        raise AiUnavailable(
            f"LLM 调用失败（已重试 {attempts - 1} 次）：{type(last_error).__name__}",
            {"task_type": task_type, "error": str(last_error)[:200]},
        )

    # --- 内部 ---
    def _call_once(
        self,
        *,
        system: str,
        user: str,
        temperature: float | None,
        max_output_tokens: int,
        tools: list[dict[str, Any]] | None,
    ) -> LLMResponse:
        settings = self.settings
        from openai import OpenAI  # 延迟导入：replay 模式不走网络也不背 SDK 成本

        client = OpenAI(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            timeout=settings.llm_timeout_seconds,
            max_retries=0,  # 重试由本类统一控制（退避 1s/3s）
        )
        kwargs: dict[str, Any] = {
            "model": settings.llm_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": settings.llm_temperature_analysis if temperature is None else temperature,
            "max_tokens": max_output_tokens,
        }
        if tools:
            kwargs["tools"] = tools
        completion = client.chat.completions.create(**kwargs)
        choice = completion.choices[0]
        message = choice.message
        text = message.content or ""
        tool_calls = getattr(message, "tool_calls", None)
        if not text and tool_calls:
            # 纯 tool-call 回合：用统一 JSON 形态回给上层
            text = json.dumps(
                {
                    "tool_calls": [
                        {
                            "name": call.function.name,
                            "args": _safe_json(call.function.arguments),
                        }
                        for call in tool_calls
                    ]
                },
                ensure_ascii=False,
            )
        usage = getattr(completion, "usage", None)
        return LLMResponse(
            text=text,
            model=f"{getattr(completion, 'model', None) or settings.llm_model}:{settings.llm_model}",
            tokens_in=int(getattr(usage, "prompt_tokens", 0) or 0),
            tokens_out=int(getattr(usage, "completion_tokens", 0) or 0),
            latency_ms=0,
            raw={"id": getattr(completion, "id", None), "finish_reason": getattr(choice, "finish_reason", None)},
        )


def _safe_json(text: str | None) -> dict[str, Any]:
    try:
        value = json.loads(text or "{}")
    except (ValueError, TypeError):
        return {"_raw": text}
    return value if isinstance(value, dict) else {"_value": value}


__all__ = [
    "BACKOFF_SECONDS",
    "DailyCostLedger",
    "LLMClient",
    "LLMResponse",
    "estimate_cost_cny",
    "estimate_tokens",
    "reset_shared_ledger",
    "shared_ledger",
]
