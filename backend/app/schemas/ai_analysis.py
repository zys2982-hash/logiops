"""AI 分析出入参（基线文档 §10.3 /ai-analyses，前端 1.5s 轮询此接口）。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class AiAnalysisStepOut(BaseModel):
    step_no: int | None = None
    step_type: str | None = None
    tool_name: str | None = None
    args: dict[str, Any] | None = None
    result_summary: str | None = None
    status: str | None = None
    duration_ms: int | None = None
    error: str | None = None
    created_at: str | None = None


class AiAnalysisOut(BaseModel):
    id: int
    exception_id: int | None = None
    analysis_no: str | None = None
    task_type: str | None = None
    status: str | None = None
    is_replay: bool | None = None
    model: str | None = None
    prompt_version: str | None = None
    risk_level_calculated: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    latency_ms: int | None = None
    reused_from_id: int | None = None
    input_hash: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    created_at: str | None = None
    output: dict[str, Any] | None = None
    steps: list[AiAnalysisStepOut] = []


class AiAnalysisStepListOut(BaseModel):
    items: list[AiAnalysisStepOut]
    total: int


class AiAnalysisRetryOut(BaseModel):
    analysis_id: int
    status: str
    reused_from_id: int | None = None
    error_code: str | None = None
    error_message: str | None = None


__all__ = [
    "AiAnalysisOut",
    "AiAnalysisRetryOut",
    "AiAnalysisStepListOut",
    "AiAnalysisStepOut",
]
