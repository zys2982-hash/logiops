"""工作区与成员管理（基线文档 §10.3【工作区】、§9.2 矩阵）。

约定：
- 创建者自动成为 OWNER；``GET /workspaces`` 只返回我加入（ACTIVE）的工作区。
- ``/workspaces/current/members/{id}`` 的 ``{id}`` 既接受成员记录 id，也接受 user_id（前端两种写法都能用）。
- 成员写操作需 ``member.manage``（VIEWER/OPERATOR → 403）；不能移除自己，不能移除 OWNER。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.api.deps import CurrentUser, DbDep, RequestContext, require
from app.core.audit import record_audit
from app.core.clock import utcnow_naive
from app.core.errors import ErrorCode, conflict, not_found, perm_denied, validation_error
from app.core.permissions import Perm
from app.models.auth import Workspace, WorkspaceMember
from app.models.enums import Role
from app.repositories import Repos
from app.schemas.workspace import MemberCreate, MemberOut, MemberUpdate, WorkspaceCreate, WorkspaceOut

router = APIRouter(prefix="/workspaces", tags=["workspaces"])

# 带权限校验的 ctx：声明成 Annotated 依赖，参数本身就是 RequestContext（§9.2 矩阵）
MemberViewCtx = Annotated[RequestContext, Depends(require(Perm.MEMBER_VIEW))]
MemberManageCtx = Annotated[RequestContext, Depends(require(Perm.MEMBER_MANAGE))]


def _member_out(member: WorkspaceMember, user: object) -> MemberOut:
    return MemberOut.from_pair(member, user)


def _resolve_member(db, workspace_id: int, member_id: int) -> WorkspaceMember:
    """先按成员记录 id 找，再按 user_id 找（两种前端写法都兼容）。"""
    repos = Repos(db, workspace_id=workspace_id)
    member = repos.members.get(member_id)
    if member is not None and member.workspace_id == workspace_id:
        return member
    membership = repos.members.get_membership(workspace_id, member_id)
    if membership is not None:
        return membership
    raise not_found("成员不存在", member_id=member_id)


@router.get("", response_model=list[WorkspaceOut], summary="我加入的工作区")
def list_workspaces(db: DbDep, user: CurrentUser) -> list[WorkspaceOut]:
    repos = Repos(db, workspace_id=None)
    result: list[WorkspaceOut] = []
    for workspace, role in repos.workspaces.list_for_user(user.id):
        item = WorkspaceOut.model_validate(workspace)
        item.role = str(role)
        item.member_count = len(repos.members.list_members(workspace.id))
        result.append(item)
    return result


@router.post(
    "",
    response_model=WorkspaceOut,
    status_code=status.HTTP_201_CREATED,
    summary="创建工作区（创建者自动 OWNER）",
)
def create_workspace(db: DbDep, user: CurrentUser, payload: WorkspaceCreate) -> WorkspaceOut:
    repos = Repos(db, workspace_id=None)
    if repos.workspaces.get_by_code(payload.code) is not None:
        raise conflict(ErrorCode.DUPLICATE_ENTITY, "工作区编码已存在", field="code")

    workspace = Workspace(
        name=payload.name,
        code=payload.code,
        owner_user_id=payload.owner_user_id or user.id,
        status="ACTIVE",
    )
    repos.workspaces.add(workspace)
    repos.members.add(
        WorkspaceMember(
            workspace_id=workspace.id,
            user_id=workspace.owner_user_id,
            role=str(Role.OWNER),
            status="ACTIVE",
            joined_at=utcnow_naive(),
            invited_by=user.id,
        )
    )
    record_audit(
        db,
        workspace_id=workspace.id,
        action="workspace.create",
        resource_type="workspace",
        resource_id=workspace.id,
        actor_id=user.id,
        after={"name": workspace.name, "code": workspace.code},
        source="MANUAL",
    )
    item = WorkspaceOut.model_validate(workspace)
    item.role = str(Role.OWNER)
    item.member_count = 1
    return item


@router.get("/current", response_model=WorkspaceOut, summary="当前工作区详情")
def current_workspace(ctx: MemberViewCtx) -> WorkspaceOut:
    item = WorkspaceOut.model_validate(ctx.workspace)
    item.role = str(ctx.role)
    item.member_count = len(ctx.repos.members.list_members(ctx.workspace_id))
    return item


@router.get("/current/members", response_model=list[MemberOut], summary="当前工作区成员")
def list_members(ctx: MemberViewCtx) -> list[MemberOut]:
    return [_member_out(member, user) for member, user in ctx.repos.members.list_members(ctx.workspace_id)]


@router.post(
    "/current/members",
    response_model=MemberOut,
    status_code=status.HTTP_201_CREATED,
    summary="按邮箱添加成员",
)
def add_member(ctx: MemberManageCtx, payload: MemberCreate) -> MemberOut:
    if payload.role == Role.OWNER:
        raise validation_error("不能直接添加 OWNER，请先添加为 ADMIN 再转让", field="role")

    user = ctx.repos.users.get_by_email(payload.email.strip().lower())
    if user is None:
        raise not_found("该邮箱尚未注册", email=payload.email)

    existing = ctx.repos.members.get_by(workspace_id=ctx.workspace_id, user_id=user.id)
    if existing is not None and existing.status == "ACTIVE":
        raise conflict(ErrorCode.DUPLICATE_ENTITY, "该用户已是工作区成员", user_id=user.id)

    if existing is not None:
        before_role = existing.status
        existing.status = "ACTIVE"
        existing.role = str(payload.role)
        existing.invited_by = ctx.user.id
        existing.joined_at = utcnow_naive()
        member = ctx.repos.members.save(existing)
        ctx.audit(
            "workspace.member_add",
            resource_type="workspace_member",
            resource_id=member.id,
            before={"status": before_role},
            after={"user_id": user.id, "role": str(payload.role), "reactivated": True},
        )
    else:
        member = ctx.repos.members.add(
            WorkspaceMember(
                workspace_id=ctx.workspace_id,
                user_id=user.id,
                role=str(payload.role),
                status="ACTIVE",
                invited_by=ctx.user.id,
                joined_at=utcnow_naive(),
            )
        )
        ctx.audit(
            "workspace.member_add",
            resource_type="workspace_member",
            resource_id=member.id,
            after={"user_id": user.id, "email": user.email, "role": str(payload.role)},
        )
    return _member_out(member, user)


@router.patch("/current/members/{member_id}", response_model=MemberOut, summary="改成员角色")
def update_member_role(ctx: MemberManageCtx, member_id: int, payload: MemberUpdate) -> MemberOut:
    member = _resolve_member(ctx.session, ctx.workspace_id, member_id)
    target = ctx.repos.users.get(member.user_id)
    if target is None:  # pragma: no cover - 数据不一致
        raise not_found("成员用户不存在", user_id=member.user_id)

    current_role = str(member.role)
    if current_role == str(Role.OWNER) and payload.role != Role.OWNER:
        raise perm_denied("不能直接修改 OWNER 的角色，请先转让 OWNER", user_id=member.user_id)
    if payload.role == Role.OWNER and ctx.role != Role.OWNER:
        raise perm_denied("仅 OWNER 可转让 OWNER", required=str(Perm.MEMBER_MANAGE))

    if current_role == str(payload.role):
        return _member_out(member, target)

    before = {"role": current_role}
    member.role = str(payload.role)
    ctx.repos.members.save(member)
    if payload.role == Role.OWNER:
        ctx.workspace.owner_user_id = member.user_id
        ctx.session.add(ctx.workspace)
    ctx.audit(
        "workspace.member_update_role",
        resource_type="workspace_member",
        resource_id=member.id,
        before=before,
        after={"user_id": member.user_id, "role": str(payload.role)},
    )
    return _member_out(member, target)


@router.delete("/current/members/{member_id}", summary="移除成员（软移除：status=REMOVED）")
def remove_member(ctx: MemberManageCtx, member_id: int) -> dict:
    member = _resolve_member(ctx.session, ctx.workspace_id, member_id)
    if member.user_id == ctx.user.id:
        raise validation_error("不能移除自己", user_id=ctx.user.id)
    if str(member.role) == str(Role.OWNER):
        raise perm_denied("不能移除 OWNER", user_id=member.user_id)
    if member.status != "ACTIVE":
        raise conflict(ErrorCode.DUPLICATE_ENTITY, "该成员已被移除", user_id=member.user_id)

    member.status = "REMOVED"
    ctx.repos.members.save(member)
    ctx.audit(
        "workspace.member_remove",
        resource_type="workspace_member",
        resource_id=member.id,
        before={"status": "ACTIVE", "role": str(member.role)},
        after={"status": "REMOVED", "user_id": member.user_id},
    )
    return {"ok": True, "member_id": member.id, "user_id": member.user_id, "status": "REMOVED"}


__all__ = ["router"]
