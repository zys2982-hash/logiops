"""跟进任务服务（基线文档 §10.3 /followups）。

状态不是状态机实体（只有 ORDER/EXCEPTION 有），因此允许 OPEN/DONE/CANCELLED 互相流转，
但 DONE 必须记 done_by/done_at，并写异常时间线与审计。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import AppError, ErrorCode, validation_error
from app.models.enums import ActorType, ExceptionEventType, FollowupSource, FollowupStatus
from app.models.exception import FollowupTask
from app.repositories import Repos
from app.services import read_models
from app.services.common import (
    add_event,
    bump_version,
    check_version,
    now_naive,
    parse_iso_naive,
    require_text,
    write_audit,
)

VALID_STATUS = {member.value for member in FollowupStatus}
VALID_SOURCE = {member.value for member in FollowupSource}


class FollowupService:
    def __init__(self, repos: Repos) -> None:
        self.repos = repos
        self.session: Session = repos.session

    def get(self, followup_id: int) -> FollowupTask:
        return self.repos.followups.get_or_404(followup_id, "跟进任务不存在")

    def create(
        self,
        *,
        exception_id: int,
        title: str,
        content: str | None = None,
        assignee_user_id: int | None = None,
        due_at: datetime | str | None = None,
        priority: str = "NORMAL",
        source: str = FollowupSource.MANUAL,
        source_approval_id: int | None = None,
        remark: str | None = None,
        actor_id: int | None = None,
    ) -> FollowupTask:
        exception = self.repos.exceptions.get_or_404(exception_id, "异常单不存在")
        source_value = str(source or FollowupSource.MANUAL).upper()
        if source_value not in VALID_SOURCE:
            source_value = str(FollowupSource.MANUAL)
        task = FollowupTask(
            workspace_id=int(self.repos.workspace_id or 0),
            exception_id=exception.id,
            title=require_text(title, "title", max_len=128),
            content=content,
            assignee_user_id=assignee_user_id,
            due_at=parse_iso_naive(due_at),
            priority=str(priority or "NORMAL").upper()[:16],
            status=str(FollowupStatus.OPEN),
            source=source_value,
            source_approval_id=source_approval_id,
            remark=remark,
        )
        self.repos.followups.add(task)
        add_event(
            self.session,
            self.repos,
            exception_id=exception.id,
            event_type=str(ExceptionEventType.COMMENT),
            from_status=exception.status,
            to_status=exception.status,
            actor_type=ActorType.USER if source_value == str(FollowupSource.MANUAL) else ActorType.AI,
            actor_id=actor_id,
            note=f"创建跟进任务：{task.title}",
            detail={"followup_task_id": task.id, "source": source_value, "due_at": task.due_at},
        )
        write_audit(
            self.session,
            self.repos,
            "followup.created",
            resource_type="followup_task",
            resource_id=task.id,
            actor_id=actor_id,
            after={
                "exception_id": exception.id,
                "title": task.title,
                "source": source_value,
                "source_approval_id": source_approval_id,
            },
        )
        return task

    def update(
        self,
        followup_id: int,
        *,
        expected_version: int | None = None,
        actor_id: int | None = None,
        title: str | None = None,
        content: str | None = None,
        assignee_user_id: int | None = None,
        due_at: datetime | str | None = None,
        priority: str | None = None,
        status: str | None = None,
        remark: str | None = None,
    ) -> FollowupTask:
        task = self.get(followup_id)
        check_version(task, expected_version, "跟进任务")
        before = {"status": task.status, "assignee_user_id": task.assignee_user_id, "due_at": task.due_at}

        if title is not None:
            task.title = require_text(title, "title", max_len=128)
        if content is not None:
            task.content = content
        if assignee_user_id is not None:
            task.assignee_user_id = assignee_user_id
        if due_at is not None:
            task.due_at = parse_iso_naive(due_at)
        if priority is not None:
            task.priority = str(priority).upper()[:16]
        if remark is not None:
            task.remark = remark
        if status is not None:
            value = str(status).upper()
            if value not in VALID_STATUS:
                raise validation_error("status 非法", fields=[{"loc": "status", "msg": value}])
            task.status = value
            if value == str(FollowupStatus.DONE):
                task.done_at = now_naive()
                task.done_by = actor_id
            elif value == str(FollowupStatus.OPEN):
                task.done_at = None
                task.done_by = None

        bump_version(task)
        self.repos.followups.save(task)
        add_event(
            self.session,
            self.repos,
            exception_id=task.exception_id,
            event_type=str(ExceptionEventType.FOLLOWUP_DONE)
            if task.status == str(FollowupStatus.DONE)
            else str(ExceptionEventType.COMMENT),
            from_status=None,
            to_status=None,
            actor_type=ActorType.USER,
            actor_id=actor_id,
            note=f"跟进任务更新：{task.title} → {task.status}",
            detail={"followup_task_id": task.id, "status": task.status},
        )
        write_audit(
            self.session,
            self.repos,
            "followup.updated",
            resource_type="followup_task",
            resource_id=task.id,
            actor_id=actor_id,
            before=before,
            after={"status": task.status, "assignee_user_id": task.assignee_user_id, "due_at": task.due_at},
        )
        return task

    def complete(
        self,
        followup_id: int,
        *,
        expected_version: int | None = None,
        done_by: int | None = None,
        remark: str | None = None,
    ) -> FollowupTask:
        task = self.get(followup_id)
        if str(task.status) == str(FollowupStatus.DONE):
            raise AppError(ErrorCode.APPROVAL_ALREADY_DECIDED, "跟进任务已完成", {"status": task.status})
        return self.update(
            followup_id,
            expected_version=expected_version,
            actor_id=done_by,
            status=str(FollowupStatus.DONE),
            remark=remark,
        )

    def list_for_exception(self, exception_id: int) -> list[dict[str, Any]]:
        self.repos.exceptions.get_or_404(exception_id, "异常单不存在")
        return [serialize(self.repos, task) for task in self.repos.followups.list_for_case(exception_id)]


def serialize(repos: Repos, task: FollowupTask) -> dict[str, Any]:
    assignee = repos.users.get(task.assignee_user_id) if task.assignee_user_id else None
    return {
        "id": task.id,
        "exception_id": task.exception_id,
        "title": task.title,
        "content": task.content,
        "assignee_user_id": task.assignee_user_id,
        "assignee_name": assignee.name if assignee else None,
        "due_at": read_models.iso(task.due_at),
        "priority": task.priority,
        "status": task.status,
        "source": task.source,
        "source_approval_id": task.source_approval_id,
        "done_at": read_models.iso(task.done_at),
        "done_by": task.done_by,
        "remark": task.remark,
        "version": task.version,
        "created_at": read_models.iso(task.created_at),
        "updated_at": read_models.iso(task.updated_at),
    }


__all__ = ["FollowupService", "serialize"]
