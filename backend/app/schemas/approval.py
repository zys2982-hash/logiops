"""审批单出入参（基线文档 §10.3 /approvals、§11.5 HITL 执行链）。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ApprovalApprove(BaseModel):
    expected_version: int
    final_payload: dict[str, Any] | None = Field(
        default=None, description="可修改 AI 建议；与 ai_payload 的差异写入 diff"
    )


class ApprovalReject(BaseModel):
    expected_version: int
    reason: str = Field(min_length=1, max_length=255)


class ApprovalBatchApprove(BaseModel):
    exception_id: int
    approval_ids: list[int] = Field(min_length=1)
    auto_execute: bool = True


class ApprovalOut(BaseModel):
    id: int
    exception_id: int | None = None
    analysis_id: int | None = None
    action_type: str | None = None
    target_type: str | None = None
    target_id: int | None = None
    status: str | None = None
    ai_payload: dict[str, Any] | None = None
    final_payload: dict[str, Any] | None = None
    diff: dict[str, Any] | None = None
    decided_by: int | None = None
    decided_at: str | None = None
    reject_reason: str | None = None
    executed_at: str | None = None
    execution_result: dict[str, Any] | None = None
    retry_count: int | None = None
    expires_at: str | None = None
    version: int | None = None
    created_at: str | None = None
    updated_at: str | None = None


class ApprovalDecisionOut(BaseModel):
    id: int
    status: str
    diff: dict[str, Any] | None = None
    execution_result: dict[str, Any] | None = None
    error_message: str | None = None
    reject_reason: str | None = None


class ApprovalListOut(BaseModel):
    items: list[ApprovalOut]
    total: int


class ApprovalBatchOut(BaseModel):
    items: list[ApprovalDecisionOut]
    total: int


__all__ = [
    "ApprovalApprove",
    "ApprovalBatchApprove",
    "ApprovalBatchOut",
    "ApprovalDecisionOut",
    "ApprovalListOut",
    "ApprovalOut",
    "ApprovalReject",
]
