"""安全审计回归测试（Lead 维护）：403/跨租户的审计必须真正落库，不能被 403 的事务回滚吃掉。"""

from __future__ import annotations

import pytest

from app.models.enums import Role


def test_permission_denied_is_audited(client, bootstrap, db_session, viewer_headers, operator_headers):
    # VIEWER 尝试写操作 → 403
    response = client.post(
        "/api/v1/customers",
        headers=viewer_headers,
        json={"code": "X-1", "name": "不该建成功", "level": "NORMAL"},
    )
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "PERM_DENIED"

    # 审计必须落库（独立会话，不受 403 回滚影响）
    db_session.expire_all()
    logs = client.get(
        "/api/v1/audit-logs",
        params={"action": "security.permission_denied"},
        headers=operator_headers,
    )
    assert logs.status_code == 200, logs.text
    assert logs.json()["total"] >= 1, "权限不足的审计被回滚了"


def test_cross_tenant_access_is_audited(client, bootstrap, db_session, operator_headers):
    fake_workspace_id = 999_999
    headers = dict(operator_headers)
    headers["X-Workspace-Id"] = str(fake_workspace_id)

    response = client.get("/api/v1/orders", headers=headers)
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "PERM_WORKSPACE_NOT_MEMBER"

    logs = client.get(
        "/api/v1/audit-logs",
        params={"action": "security.cross_tenant_denied"},
        headers=operator_headers,
    )
    assert logs.status_code == 200, logs.text
    assert logs.json()["total"] >= 1, "跨租户拒绝的审计被回滚了"


@pytest.mark.parametrize("role", [Role.OWNER, Role.ADMIN, Role.OPERATOR, Role.VIEWER])
def test_every_role_has_expected_write_permission(client, bootstrap, role: Role):
    """角色 × 操作矩阵的双向断言（改一行矩阵就会红）。"""
    from app.core.permissions import Perm, has_perm

    assert has_perm(role, Perm.ORDER_VIEW) is True
    if role in {Role.OWNER, Role.ADMIN}:
        assert has_perm(role, Perm.CUSTOMER_MANAGE) is True
    else:
        assert has_perm(role, Perm.CUSTOMER_MANAGE) is False
    assert has_perm(role, Perm.EXCEPTION_HANDLE) is (role != Role.VIEWER)
