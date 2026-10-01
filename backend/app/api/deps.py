"""FastAPI 依赖：当前用户、当前工作区、权限校验、分页参数。

越权策略（基线文档 §9.1）：跨租户一律 404；角色不足 403；都要留审计。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, Query, Request
from sqlalchemy.orm import Session

from app.core.audit import record_audit, record_audit_isolated
from app.core.errors import AppError, ErrorCode, perm_denied
from app.core.permissions import Perm, has_perm
from app.core.security import decode_token
from app.db.session import get_db
from app.models.auth import User, Workspace
from app.models.enums import Role
from app.repositories import Repos


@dataclass
class PageParams:
    page: int = 1
    page_size: int = 20

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


def page_params(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> PageParams:
    return PageParams(page=page, page_size=page_size)


PageDep = Annotated[PageParams, Depends(page_params)]
DbDep = Annotated[Session, Depends(get_db)]


def _extract_token(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise AppError(ErrorCode.AUTH_TOKEN_MISSING, "缺少登录凭证")
    token = authorization.split(" ", 1)[1].strip()
    if not token:
        raise AppError(ErrorCode.AUTH_TOKEN_MISSING, "缺少登录凭证")
    return token


def get_current_user(
    db: DbDep,
    authorization: Annotated[str | None, Header()] = None,
) -> User:
    payload = decode_token(_extract_token(authorization))
    try:
        user_id = int(payload.get("sub", "0"))
    except (TypeError, ValueError) as exc:
        raise AppError(ErrorCode.AUTH_TOKEN_INVALID, "登录凭证无效") from exc
    user = db.get(User, user_id)
    if user is None or user.status != "ACTIVE":
        raise AppError(ErrorCode.AUTH_TOKEN_INVALID, "登录凭证无效")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


@dataclass
class RequestContext:
    user: User
    workspace: Workspace
    role: Role
    repos: Repos
    request_id: str | None = None
    ip: str | None = None
    user_agent: str | None = None

    @property
    def session(self) -> Session:
        return self.repos.session

    @property
    def workspace_id(self) -> int:
        return self.workspace.id

    def audit(self, action: str, **kwargs: object) -> None:
        record_audit(
            self.session,
            workspace_id=self.workspace.id,
            action=action,
            actor_id=self.user.id,
            request_id=self.request_id,
            ip=self.ip,
            user_agent=self.user_agent,
            **kwargs,  # type: ignore[arg-type]
        )


def get_context(
    db: DbDep,
    request: Request,
    user: CurrentUser,
    x_workspace_id: Annotated[int | None, Header(alias="X-Workspace-Id")] = None,
) -> RequestContext:
    repos = Repos(db, workspace_id=None)
    memberships = repos.workspaces.list_for_user(user.id)
    request_id = getattr(request.state, "request_id", None)
    client_ip = request.client.host if request.client else None

    # 安全审计记在"用户自己的工作区"，这样本租户管理员才能在审计页看到越权尝试；
    # 被尝试的租户 id 放在 after 里（而不是写进别人的日志）。
    audit_workspace_id = memberships[0][0].id if memberships else (x_workspace_id or 0)

    if not memberships:
        record_audit_isolated(
            workspace_id=audit_workspace_id,
            action="security.no_workspace",
            resource_type="user",
            resource_id=user.id,
            actor_id=user.id,
            source="SYSTEM",
            request_id=request_id,
            ip=client_ip,
        )
        raise AppError(ErrorCode.PERM_WORKSPACE_NOT_MEMBER, "尚未加入任何工作区")

    selected: tuple[Workspace, str] | None = None
    if x_workspace_id is not None:
        for workspace, role in memberships:
            if workspace.id == x_workspace_id:
                selected = (workspace, role)
                break
        if selected is None:
            # 跨租户访问：用独立会话写审计（请求会话即将因抛错回滚）
            record_audit_isolated(
                workspace_id=audit_workspace_id,
                action="security.cross_tenant_denied",
                resource_type="workspace",
                resource_id=audit_workspace_id,
                actor_id=user.id,
                after={"attempted_workspace_id": x_workspace_id, "path": request.url.path},
                source="SYSTEM",
                request_id=request_id,
                ip=client_ip,
            )
            raise AppError(ErrorCode.PERM_WORKSPACE_NOT_MEMBER, "不属于该工作区")
    else:
        selected = memberships[0]

    workspace, role = selected
    ctx = RequestContext(
        user=user,
        workspace=workspace,
        role=Role(role),
        repos=Repos(db, workspace_id=workspace.id),
        request_id=request_id,
        ip=client_ip,
        user_agent=request.headers.get("user-agent"),
    )
    return ctx


ContextDep = Annotated[RequestContext, Depends(get_context)]


def require(perm: Perm):
    """生成一个"需要某权限"的依赖。"""

    def _dependency(ctx: ContextDep) -> RequestContext:
        if not has_perm(ctx.role, perm):
            # 权限不足：同样用独立会话，否则这条安全审计会随 403 一起回滚
            record_audit_isolated(
                workspace_id=ctx.workspace_id,
                action="security.permission_denied",
                resource_type="permission",
                actor_id=ctx.user.id,
                after={"required": str(perm), "role": str(ctx.role)},
                source="SYSTEM",
                request_id=ctx.request_id,
                ip=ctx.ip,
            )
            raise perm_denied("当前角色无权执行该操作", required=str(perm), role=str(ctx.role))
        return ctx

    return _dependency


def require_any(*perms: Perm):
    def _dependency(ctx: ContextDep) -> RequestContext:
        if not any(has_perm(ctx.role, perm) for perm in perms):
            raise perm_denied("当前角色无权执行该操作", required=[str(p) for p in perms], role=str(ctx.role))
        return ctx

    return _dependency


def permissions_of(ctx: RequestContext) -> list[str]:
    from app.core.permissions import perms_for

    return sorted(str(perm) for perm in perms_for(ctx.role))
