"""跟进任务接口（基线文档 §10.3 【跟进任务】/followups）。"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, status

from app.api.deps import RequestContext, require
from app.core.permissions import Perm
from app.schemas.followup import FollowupCreate, FollowupListOut, FollowupOut, FollowupUpdate
from app.services.followups import FollowupService, serialize

router = APIRouter(tags=["followups"])

FollowupView = Annotated[RequestContext, Depends(require(Perm.EXCEPTION_VIEW))]
FollowupWrite = Annotated[RequestContext, Depends(require(Perm.FOLLOWUP_WRITE))]


@router.get("/exceptions/{exception_id}/followups", response_model=FollowupListOut, summary="异常下的跟进任务")
def list_followups(ctx: FollowupView, exception_id: int) -> Any:
    items = FollowupService(ctx.repos).list_for_exception(exception_id)
    return {"items": items, "total": len(items)}


@router.post(
    "/followups",
    response_model=FollowupOut,
    status_code=status.HTTP_201_CREATED,
    summary="手工创建跟进任务（source=MANUAL）",
)
def create_followup(ctx: FollowupWrite, payload: FollowupCreate) -> Any:
    task = FollowupService(ctx.repos).create(
        exception_id=payload.exception_id,
        title=payload.title,
        content=payload.content,
        assignee_user_id=payload.assignee_user_id,
        due_at=payload.due_at,
        priority=payload.priority,
        source=payload.source,
        actor_id=ctx.user.id,
    )
    return serialize(ctx.repos, task)


@router.patch(
    "/followups/{followup_id}",
    response_model=FollowupOut,
    summary="改 assignee/due_at/status（DONE 记 done_by/done_at）",
)
def update_followup(ctx: FollowupWrite, followup_id: int, payload: FollowupUpdate) -> Any:
    fields = payload.model_dump(exclude_unset=True, exclude={"expected_version"})
    task = FollowupService(ctx.repos).update(
        followup_id,
        expected_version=payload.expected_version,
        actor_id=ctx.user.id,
        **fields,
    )
    return serialize(ctx.repos, task)


@router.get("/followups/{followup_id}", response_model=FollowupOut, summary="跟进任务详情")
def get_followup(ctx: FollowupView, followup_id: int) -> Any:
    return serialize(ctx.repos, FollowupService(ctx.repos).get(followup_id))


__all__ = ["router"]
