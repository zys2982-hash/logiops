"""LLM 客户端：超时/重试/额度/token 上限/无 Key 降级（全部离线，不调用真实 LLM）。"""

from __future__ import annotations

import pytest

from app.ai.errors import AiUnavailable
from app.ai.llm import DailyCostLedger, LLMClient, LLMResponse, estimate_cost_cny, estimate_tokens
from app.core.config import Settings


def _settings(**overrides) -> Settings:
    base = {
        "llm_api_key": "test-key",
        "llm_base_url": "https://example.invalid/v1",
        "llm_model": "deepseek-chat",
        "llm_timeout_seconds": 5,
        "llm_max_retries": 2,
        "llm_max_input_tokens": 8000,
        "llm_max_output_tokens": 1500,
        "llm_daily_cost_limit_cny": 20.0,
    }
    base.update(overrides)
    return Settings(**base)


def test_missing_api_key_degrades_to_ai_unavailable(monkeypatch):
    settings = _settings(llm_api_key="")
    client = LLMClient(settings=settings, ledger=DailyCostLedger(settings.llm_daily_cost_limit_cny))
    with pytest.raises(AiUnavailable) as exc:
        client.complete(task_type="ANALYZE_EXCEPTION", system="s", user="u")
    assert "LLM_API_KEY" in exc.value.message
    assert str(exc.value.code) == "LLM_UNAVAILABLE"


def test_input_token_cap_degrades():
    settings = _settings(llm_max_input_tokens=10)
    client = LLMClient(settings=settings, ledger=DailyCostLedger(settings.llm_daily_cost_limit_cny))
    with pytest.raises(AiUnavailable) as exc:
        client.complete(task_type="ANALYZE_EXCEPTION", system="s" * 100, user="u" * 100)
    assert "token 上限" in exc.value.message


def test_retry_backoff_is_one_and_three_seconds(monkeypatch):
    settings = _settings()
    sleeps: list[float] = []
    client = LLMClient(
        settings=settings,
        ledger=DailyCostLedger(settings.llm_daily_cost_limit_cny),
        sleep=sleeps.append,
    )
    attempts: list[int] = []

    def fake_call(**_kwargs):
        attempts.append(1)
        if len(attempts) < 3:
            raise RuntimeError("5xx")
        return LLMResponse(text="{}", model="deepseek-chat", tokens_in=10, tokens_out=5, latency_ms=0)

    monkeypatch.setattr(client, "_call_once", fake_call)
    response = client.complete(task_type="PARSE_MESSAGE", system="s", user="u")
    assert response.text == "{}"
    assert len(attempts) == 3
    assert sleeps == [1.0, 3.0]


def test_retries_exhausted_raises_ai_unavailable(monkeypatch):
    settings = _settings()
    client = LLMClient(settings=settings, ledger=DailyCostLedger(20.0), sleep=lambda _: None)

    def always_fail(**_kwargs):
        raise TimeoutError("read timeout")

    monkeypatch.setattr(client, "_call_once", always_fail)
    with pytest.raises(AiUnavailable) as exc:
        client.complete(task_type="ANALYZE_EXCEPTION", system="s", user="u")
    assert "已重试 2 次" in exc.value.message
    assert "TimeoutError" in exc.value.message


def test_daily_cost_limit_blocks_calls():
    ledger = DailyCostLedger(limit_cny=0.0000001)
    ledger.record(1.0)
    with pytest.raises(AiUnavailable) as exc:
        ledger.ensure_budget(0.01)
    assert "额度已用完" in exc.value.message


def test_daily_cost_ledger_accumulates():
    ledger = DailyCostLedger(limit_cny=1.0)
    assert ledger.remaining_cny == 1.0
    ledger.record(0.25)
    assert ledger.spent_cny == pytest.approx(0.25)
    assert ledger.remaining_cny == pytest.approx(0.75)
    ledger.ensure_budget(0.5)
    with pytest.raises(AiUnavailable):
        ledger.ensure_budget(0.8)


def test_token_and_cost_estimates_are_monotonic():
    assert estimate_tokens("") == 0
    assert estimate_tokens("中文" * 10) < estimate_tokens("中文" * 40)
    assert estimate_cost_cny("deepseek-chat", 1000, 1000) > 0
    assert estimate_cost_cny("deepseek-chat", 2000, 1000) > estimate_cost_cny("deepseek-chat", 1000, 1000)
