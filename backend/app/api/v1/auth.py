"""认证接口（基线文档 §10.3【认证】）。

- 登录返回 ``{access_token, token_type, expires_in, user}``。
- ``/auth/me`` 额外返回当前工作区角色与权限列表（``perms_for``）以及我加入的工作区。
- 登出无服务端黑名单（明确声明）：仅写审计，token 由前端丢弃。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, status

from app.api.deps import ContextDep, CurrentUser, DbDep
from app.core.audit import record_audit
from app.core.clock import utcnow_naive
from app.core.errors import AppError, ErrorCode, conflict, validation_error
from app.core.permissions import perms_for
from app.core.security import create_access_token, hash_password, verify_password
from app.models.auth import User, Workspace, WorkspaceMember
from app.models.enums import Role
from app.repositories import Repos
from app.schemas.auth import (
    ChangePasswordRequest,
    ChangePasswordResponse,
    LoginRequest,
    LoginResponse,
    LogoutResponse,
    MeOut,
    RegisterRequest,
    RegisterResponse,
    UserOut,
    WorkspaceBrief,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _memberships(db, user_id: int) -> list[tuple[Workspace, str]]:
    return Repos(db, workspace_id=None).workspaces.list_for_user(user_id)


def _create_default_workspace(db, user: User) -> Workspace:
    """注册时自动建一个工作区并让创建者成为 OWNER（否则新账号无工作区可用）。"""
    repos = Repos(db, workspace_id=None)
    code = f"WS-{user.id:04d}"
    if repos.workspaces.get_by_code(code) is not None:  # pragma: no cover - 极小概率
        code = f"WS-{user.id:04d}-{utcnow_naive().strftime('%H%M%S')}"
    workspace = Workspace(name=f"{user.name}的工作区", code=code, owner_user_id=user.id, status="ACTIVE")
    repos.workspaces.add(workspace)
    member = WorkspaceMember(
        workspace_id=workspace.id,
        user_id=user.id,
        role=str(Role.OWNER),
        status="ACTIVE",
        joined_at=utcnow_naive(),
    )
    repos.members.add(member)
    return workspace


@router.post(
    "/register",
    response_model=RegisterResponse,
    status_code=status.HTTP_201_CREATED,
    summary="注册（自动创建默认工作区并成为 OWNER）",
)
def register(db: DbDep, payload: RegisterRequest) -> RegisterResponse:
    repos = Repos(db, workspace_id=None)
    if repos.users.get_by_email(payload.email) is not None:
        raise conflict(ErrorCode.DUPLICATE_ENTITY, "该邮箱已注册", field="email")

    user = User(
        email=payload.email,
        name=payload.name,
        password_hash=hash_password(payload.password),
        status="ACTIVE",
    )
    repos.users.add(user)
    workspace = _create_default_workspace(db, user)

    record_audit(
        db,
        workspace_id=workspace.id,
        action="auth.register",
        resource_type="user",
        resource_id=user.id,
        actor_id=user.id,
        after={"email": user.email, "name": user.name, "workspace_id": workspace.id},
        source="MANUAL",
    )
    return RegisterResponse(user=UserOut.from_model(user), default_workspace_id=workspace.id)


@router.post("/login", response_model=LoginResponse, summary="登录")
def login(db: DbDep, payload: LoginRequest) -> LoginResponse:
    repos = Repos(db, workspace_id=None)
    user = repos.users.get_by_email(payload.email)
    if user is None or not verify_password(payload.password, user.password_hash):
        raise AppError(ErrorCode.AUTH_INVALID_CREDENTIALS, "账号或密码错误")
    if user.status != "ACTIVE":
        raise AppError(ErrorCode.AUTH_INVALID_CREDENTIALS, "账号已停用")

    user.last_login_at = utcnow_naive()
    repos.users.save(user)

    memberships = _memberships(db, user.id)
    if memberships:
        record_audit(
            db,
            workspace_id=memberships[0][0].id,
            action="auth.login",
            resource_type="user",
            resource_id=user.id,
            actor_id=user.id,
            after={"email": user.email},
            source="MANUAL",
        )
    token, expires_in = create_access_token(user.id, user.email)
    return LoginResponse(
        access_token=token,
        token_type="bearer",
        expires_in=expires_in,
        user=UserOut.from_model(user),
    )


@router.get("/me", response_model=MeOut, summary="当前用户 + 角色 + 权限 + 工作区列表")
def me(
    db: DbDep,
    user: CurrentUser,
    x_workspace_id: Annotated[int | None, Header(alias="X-Workspace-Id")] = None,
) -> MeOut:
    memberships = _memberships(db, user.id)
    briefs = [
        WorkspaceBrief(id=workspace.id, name=workspace.name, code=workspace.code, role=str(role))
        for workspace, role in memberships
    ]

    role: str | None = None
    workspace_brief: WorkspaceBrief | None = None
    if memberships:
        selected = None
        if x_workspace_id is not None:
            selected = next(((ws, r) for ws, r in memberships if ws.id == x_workspace_id), None)
            if selected is None:
                record_audit(
                    db,
                    workspace_id=x_workspace_id,
                    action="security.cross_tenant_denied",
                    resource_type="workspace",
                    resource_id=x_workspace_id,
                    actor_id=user.id,
                    source="SYSTEM",
                )
                raise AppError(ErrorCode.PERM_WORKSPACE_NOT_MEMBER, "不属于该工作区")
        else:
            selected = memberships[0]
        role = str(selected[1])
        workspace_brief = WorkspaceBrief(
            id=selected[0].id, name=selected[0].name, code=selected[0].code, role=role
        )

    permissions = sorted(str(perm) for perm in perms_for(role)) if role else []
    return MeOut(
        user=UserOut.from_model(user),
        role=role,
        permissions=permissions,
        workspace_id=workspace_brief.id if workspace_brief else None,
        workspace=workspace_brief,
        workspaces=briefs,
    )


@router.post("/logout", response_model=LogoutResponse, summary="登出（客户端丢弃 token）")
def logout(ctx: ContextDep) -> LogoutResponse:
    ctx.audit("auth.logout", resource_type="user", resource_id=ctx.user.id)
    return LogoutResponse()


@router.post(
    "/password",
    response_model=ChangePasswordResponse,
    summary="修改当前用户密码（需验原密码）",
)
def change_password(db: DbDep, user: CurrentUser, payload: ChangePasswordRequest) -> ChangePasswordResponse:
    """改「自己」的密码：目标用户取自 token，接口不接受 user_id/email 参数 —— 结构上不可能越权改他人。

    注意：JWT 无状态、服务端不维护黑名单（与 logout 同一口径），所以改完密码后**已签发的旧 token
    在过期前仍然有效**；要立刻失效只能由前端重新登录、丢弃旧 token。
    """
    if not verify_password(payload.old_password, user.password_hash):
        # 刻意用 422 而不是 401：前端 axios 拦截器把**任何 401** 都当"会话失效"并执行
        # logoutLocal()（见 frontend/src/stores/auth.ts 的 onUnauthorized）——
        # 这里若返回 401，用户只是打错一次原密码就会被踢下线。语义上也说得通：
        # 请求本身已通过认证，错的是提交上来的 old_password 字段。
        raise validation_error("原密码不正确", field="old_password")

    repos = Repos(db, workspace_id=None)
    user.password_hash = hash_password(payload.new_password)
    repos.users.save(user)

    memberships = _memberships(db, user.id)
    if memberships:
        # 只记"谁在什么时候改了自己的密码"，不记任何密码/哈希内容
        record_audit(
            db,
            workspace_id=memberships[0][0].id,
            action="auth.password_changed",
            resource_type="user",
            resource_id=user.id,
            actor_id=user.id,
            after={"email": user.email},
            source="MANUAL",
        )
    return ChangePasswordResponse()


__all__ = ["router"]
