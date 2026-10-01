"""RBAC 权限矩阵（基线文档 §9.2）。

矩阵是"唯一真相"，改一行就能靠单测发现影响面。
"""

from __future__ import annotations

from enum import StrEnum

from app.models.enums import Role


class Perm(StrEnum):
    # 查看类
    DASHBOARD_VIEW = "dashboard.view"
    CUSTOMER_VIEW = "customer.view"
    CARRIER_VIEW = "carrier.view"
    VEHICLE_VIEW = "vehicle.view"
    DRIVER_VIEW = "driver.view"
    ORDER_VIEW = "order.view"
    TRACKING_VIEW = "tracking.view"
    SLA_VIEW = "sla.view"
    EXCEPTION_VIEW = "exception.view"
    AUDIT_VIEW = "audit.view"
    KNOWLEDGE_VIEW = "knowledge.view"
    MEMBER_VIEW = "member.view"
    # 写类
    CUSTOMER_MANAGE = "customer.manage"
    CARRIER_MANAGE = "carrier.manage"
    VEHICLE_MANAGE = "vehicle.manage"
    DRIVER_MANAGE = "driver.manage"
    ORDER_MANAGE = "order.manage"
    TRACKING_WRITE = "tracking.write"
    SLA_MANAGE = "sla.manage"
    EXCEPTION_CREATE = "exception.create"
    EXCEPTION_HANDLE = "exception.handle"
    EXCEPTION_FORCE_CLOSE = "exception.force_close"
    APPROVAL_DECIDE = "approval.decide"
    FOLLOWUP_WRITE = "followup.write"
    NOTIFICATION_APPROVE = "notification.approve"
    MEMBER_MANAGE = "member.manage"
    KNOWLEDGE_MANAGE = "knowledge.manage"
    WORKSPACE_DELETE = "workspace.delete"
    DEMO_CONTROL = "demo.control"


VIEW_PERMS: frozenset[Perm] = frozenset(
    {
        Perm.DASHBOARD_VIEW,
        Perm.CUSTOMER_VIEW,
        Perm.CARRIER_VIEW,
        Perm.VEHICLE_VIEW,
        Perm.DRIVER_VIEW,
        Perm.ORDER_VIEW,
        Perm.TRACKING_VIEW,
        Perm.SLA_VIEW,
        Perm.EXCEPTION_VIEW,
        Perm.AUDIT_VIEW,
        Perm.KNOWLEDGE_VIEW,
        Perm.MEMBER_VIEW,
    }
)

OPERATOR_PERMS: frozenset[Perm] = VIEW_PERMS | {
    Perm.TRACKING_WRITE,
    Perm.EXCEPTION_CREATE,
    Perm.EXCEPTION_HANDLE,
    Perm.APPROVAL_DECIDE,
    Perm.FOLLOWUP_WRITE,
    Perm.NOTIFICATION_APPROVE,
    Perm.DEMO_CONTROL,
}

ADMIN_PERMS: frozenset[Perm] = OPERATOR_PERMS | {
    Perm.CUSTOMER_MANAGE,
    Perm.CARRIER_MANAGE,
    Perm.VEHICLE_MANAGE,
    Perm.DRIVER_MANAGE,
    Perm.ORDER_MANAGE,
    Perm.SLA_MANAGE,
    Perm.EXCEPTION_FORCE_CLOSE,
    Perm.MEMBER_MANAGE,
    Perm.KNOWLEDGE_MANAGE,
}

ROLE_PERMS: dict[Role, frozenset[Perm]] = {
    Role.VIEWER: VIEW_PERMS,
    Role.OPERATOR: OPERATOR_PERMS,
    Role.ADMIN: ADMIN_PERMS,
    Role.OWNER: frozenset(Perm),
}


def perms_for(role: Role | str) -> frozenset[Perm]:
    if isinstance(role, str):
        role = Role(role)
    return ROLE_PERMS[role]


def has_perm(role: Role | str, perm: Perm) -> bool:
    return perm in perms_for(role)
