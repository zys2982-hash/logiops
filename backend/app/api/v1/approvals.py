"""审批接口（基线文档 §10.3 【人工确认】/approvals、§11.5 执行链）。

- GET /exceptions/{id}/approvals 返回平数组（前端逐条展示"AI 建议/依据/[编辑][批准][驳回]"）。
- approve 成功后 status=EXECUTED 并返回 execution_result；失败 status=FAILED 可 /execute 重试。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from app.api.deps import RequestContext, require
from app.core.permissions import Perm
from app.schemas.approval import (
    ApprovalApprove,
    ApprovalBatchApprove,
    ApprovalBatchOut,
    ApprovalDecisionOut,
    ApprovalOut,
    ApprovalReject,
)
from app.services.approvals import ApprovalExecutor, serialize

router = APIRouter(tags=["approvals"])

ApprovalView = Annotated[RequestContext, Depends(require(Perm.EXCEPTION_VIEW))]
ApprovalDecide = Annotated[RequestContext, Depends(require(Perm.APPROVAL_DECIDE))]


@router.get("/exceptions/{exception_id}/approvals", response_model=list[ApprovalOut], summary="异常下的审批单")
def list_approvals(ctx: ApprovalView, exception_id: int) -> Any:
    return ApprovalExecutor(ctx.repos).list_for_exception(exception_id)


@router.get("/approvals/{approval_id}", response_model=ApprovalOut, summary="审批单详情")
def get_approval(ctx: ApprovalView, approval_id: int) -> Any:
    return serialize(ApprovalExecutor(ctx.repos).get(approval_id))


@router.post(
    "/approvals/{approval_id}/approve",
    response_model=ApprovalDecisionOut,
    summary="批准并执行（可修改 AI 建议，返回 diff 与 execution_result）",
)
def approve(ctx: ApprovalDecide, approval_id: int, payload: ApprovalApprove) -> Any:
    return ApprovalExecutor(ctx.repos).approve(
        approval_id,
        actor_id=ctx.user.id,
        final_payload=payload.final_payload,
        expected_version=payload.expected_version,
    )


@router.post("/approvals/{approval_id}/reject", response_model=ApprovalDecisionOut, summary="驳回（必填 reason）")
def reject(ctx: ApprovalDecide, approval_id: int, payload: ApprovalReject) -> Any:
    return ApprovalExecutor(ctx.repos).reject(
        approval_id,
        actor_id=ctx.user.id,
        reason=payload.reason,
        expected_version=payload.expected_version,
    )


@router.post(
    "/approvals/batch-approve",
    response_model=ApprovalBatchOut,
    summary="批量批准（逐条执行，部分失败可单独重试）",
)
def batch_approve(ctx: ApprovalDecide, payload: ApprovalBatchApprove) -> Any:
    items = ApprovalExecutor(ctx.repos).batch_approve(
        exception_id=payload.exception_id,
        approval_ids=payload.approval_ids,
        actor_id=ctx.user.id,
        auto_execute=payload.auto_execute,
    )
    return {"items": items, "total": len(items)}


@router.post("/approvals/{approval_id}/execute", response_model=ApprovalDecisionOut, summary="重试执行（仅 FAILED）")
def execute(ctx: ApprovalDecide, approval_id: int) -> Any:
    return ApprovalExecutor(ctx.repos).retry(approval_id, actor_id=ctx.user.id)


__all__ = ["router"]
