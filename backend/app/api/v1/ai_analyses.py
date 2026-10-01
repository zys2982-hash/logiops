"""AI 分析接口（基线文档 §10.3 【AI 分析】/ai-analyses，前端 1.5s 轮询）。"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from app.api.deps import RequestContext, require
from app.core.permissions import Perm
from app.schemas.ai_analysis import (
    AiAnalysisOut,
    AiAnalysisRetryOut,
    AiAnalysisStepListOut,
    AiAnalysisStepOut,
)
from app.services.exceptions import ExceptionService
from app.services.serializers import analysis_out, step_out

router = APIRouter(prefix="/ai-analyses", tags=["ai-analyses"])

AnalysisView = Annotated[RequestContext, Depends(require(Perm.EXCEPTION_VIEW))]
AnalysisHandle = Annotated[RequestContext, Depends(require(Perm.EXCEPTION_HANDLE))]


@router.get("/{analysis_id}", response_model=AiAnalysisOut, summary="分析状态 + output + 步骤进度")
def get_analysis(ctx: AnalysisView, analysis_id: int) -> Any:
    analysis = ctx.repos.analyses.get_or_404(analysis_id, "分析任务不存在")
    return analysis_out(analysis)


@router.get("/{analysis_id}/steps", response_model=AiAnalysisStepListOut, summary="工具调用明细")
def get_steps(ctx: AnalysisView, analysis_id: int) -> Any:
    ctx.repos.analyses.get_or_404(analysis_id, "分析任务不存在")
    steps = ctx.repos.analysis_steps.list_for_analysis(analysis_id)
    items: list[AiAnalysisStepOut] = [step_out(step) for step in steps]
    return {"items": items, "total": len(items)}


@router.post("/{analysis_id}/retry", response_model=AiAnalysisRetryOut, summary="FAILED 时重跑（复用 input_hash）")
def retry_analysis(ctx: AnalysisHandle, analysis_id: int) -> Any:
    return ExceptionService(ctx.repos).retry_analysis(analysis_id, actor_id=ctx.user.id)


__all__ = ["router"]
