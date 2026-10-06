"""SLA 规则维护（基线文档 §10.3【SLA 规则】、§8.3）。

用户口径（2026-10-06）：**只保留两种规则** ——「VIP 客户等级规则」（`CUSTOMER_LEVEL:VIP`）与「默认规则」（`DEFAULT`）。
「指定客户（`CUSTOMER`，例如 VIP-01 专属）」已下线：界面不再提供该作用域，本模块创建/修改成该作用域会返回 422。
（`app.rules.sla.match_rule` 仍保留 `CUSTOMER` 分支，只为兼容历史库里已存在的旧规则。）

规则匹配由 ``app.rules.sla.match_rule`` 决定：具体客户（历史）> 客户等级 > 默认；同一档取 priority 最小者。
唯一键 ``(workspace_id, scope_type, scope_value)``：手工查重后返回 409 DUPLICATE_ENTITY。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from fastapi import status as http_status

from app.api.deps import RequestContext, require
from app.core.errors import ErrorCode, conflict, validation_error
from app.core.permissions import Perm
from app.models.master import SlaRule
from app.schemas.common import patch_payload
from app.schemas.sla import SlaRuleCreate, SlaRuleOut, SlaRuleUpdate

router = APIRouter(prefix="/sla-rules", tags=["sla"])

ViewCtx = Annotated[RequestContext, Depends(require(Perm.SLA_VIEW))]
ManageCtx = Annotated[RequestContext, Depends(require(Perm.SLA_MANAGE))]

# 只开放这两种作用域（顺序 = 界面展示顺序）
SUPPORTED_SCOPE_TYPES = ("DEFAULT", "CUSTOMER_LEVEL")


def _find_same_scope(ctx: RequestContext, scope_type: str, scope_value: str | None, exclude_id: int | None = None):
    for rule in ctx.repos.sla_rules.all(order_by=[SlaRule.priority.asc(), SlaRule.id.asc()]):
        if rule.id == exclude_id:
            continue
        if str(rule.scope_type) == scope_type and (rule.scope_value or None) == (scope_value or None):
            return rule
    return None


def _validate_scope(scope_type: str, scope_value: str | None) -> None:
    if scope_type not in SUPPORTED_SCOPE_TYPES:
        raise validation_error(
            "「指定客户」作用域已下线：SLA 规则只保留「按客户等级（VIP）」与「默认」两种",
            field="scope_type",
        )
    if scope_type == "DEFAULT":
        if scope_value:
            raise validation_error("DEFAULT 作用域不能填 scope_value", field="scope_value")
        return
    if not scope_value:
        raise validation_error("CUSTOMER_LEVEL 作用域必须填 scope_value（如 VIP）", field="scope_value")


@router.get("", response_model=list[SlaRuleOut], summary="SLA 规则列表（按 priority 升序）")
def list_sla_rules(
    ctx: ViewCtx,
    scope_type: Annotated[str | None, Query(max_length=16)] = None,
    is_active: Annotated[bool | None, Query()] = None,
) -> list[SlaRuleOut]:
    filters: list[Any] = []
    if scope_type:
        filters.append(SlaRule.scope_type == scope_type)
    if is_active is not None:
        filters.append(SlaRule.is_active.is_(is_active))
    rules = ctx.repos.sla_rules.all(filters=filters, order_by=[SlaRule.priority.asc(), SlaRule.id.asc()])
    return [SlaRuleOut.from_model(rule) for rule in rules]


@router.post("", response_model=SlaRuleOut, status_code=http_status.HTTP_201_CREATED, summary="创建 SLA 规则")
def create_sla_rule(ctx: ManageCtx, payload: SlaRuleCreate) -> SlaRuleOut:
    scope_type = str(payload.scope_type)
    _validate_scope(scope_type, payload.scope_value)
    if _find_same_scope(ctx, scope_type, payload.scope_value) is not None:
        raise conflict(
            ErrorCode.DUPLICATE_ENTITY,
            "同作用域的 SLA 规则已存在",
            scope_type=scope_type,
            scope_value=payload.scope_value,
        )

    rule = SlaRule(
        workspace_id=ctx.workspace_id,
        name=payload.name,
        scope_type=scope_type,
        scope_value=payload.scope_value,
        deadline_offset_hours=payload.deadline_offset_hours,
        max_delay_minutes=payload.max_delay_minutes,
        priority=payload.priority,
        action_policy_json=payload.action_policy_json,
        description=payload.description,
        is_active=payload.is_active,
    )
    ctx.repos.sla_rules.add(rule)
    ctx.audit(
        "sla_rule.create",
        resource_type="sla_rule",
        resource_id=rule.id,
        after={
            "name": rule.name,
            "scope_type": rule.scope_type,
            "scope_value": rule.scope_value,
            "deadline_offset_hours": rule.deadline_offset_hours,
            "max_delay_minutes": rule.max_delay_minutes,
        },
    )
    return SlaRuleOut.from_model(rule)


@router.get("/{rule_id}", response_model=SlaRuleOut, summary="SLA 规则详情")
def get_sla_rule(ctx: ViewCtx, rule_id: int) -> SlaRuleOut:
    return SlaRuleOut.from_model(ctx.repos.sla_rules.get_or_404(rule_id, "SLA 规则不存在"))


@router.patch("/{rule_id}", response_model=SlaRuleOut, summary="修改 SLA 规则")
def update_sla_rule(ctx: ManageCtx, rule_id: int, payload: SlaRuleUpdate) -> SlaRuleOut:
    rule = ctx.repos.sla_rules.get_or_404(rule_id, "SLA 规则不存在")
    if payload.expected_version is not None and payload.expected_version != rule.version:
        raise conflict(
            ErrorCode.OPTIMISTIC_LOCK_CONFLICT,
            "数据已被他人更新，请刷新后重试",
            expected_version=payload.expected_version,
            current_version=rule.version,
        )

    changes = patch_payload(payload)
    next_scope_type = str(changes.get("scope_type", rule.scope_type))
    next_scope_value = changes.get("scope_value", rule.scope_value)
    _validate_scope(next_scope_type, next_scope_value)
    if _find_same_scope(ctx, next_scope_type, next_scope_value, exclude_id=rule.id) is not None:
        raise conflict(
            ErrorCode.DUPLICATE_ENTITY,
            "同作用域的 SLA 规则已存在",
            scope_type=next_scope_type,
            scope_value=next_scope_value,
        )

    before = {key: getattr(rule, key) for key in changes}
    for key, value in changes.items():
        setattr(rule, key, value)
    rule.version = int(rule.version or 1) + 1
    ctx.repos.sla_rules.save(rule)
    ctx.audit("sla_rule.update", resource_type="sla_rule", resource_id=rule.id, before=before, after=changes)
    return SlaRuleOut.from_model(rule)


__all__ = ["router"]
