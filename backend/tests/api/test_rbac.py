"""RBAC × 多租户隔离测试（基线文档 §9.1 / §9.2、S5 验收标准）。

覆盖：VIEWER 写操作 403、OPERATOR 不能改主数据、跨工作区 404、非成员 403、成员管理权限。
"""

from __future__ import annotations

OPERATOR = "operator@logiops.dev"


def _new_workspace(client, owner_headers, code: str = "WS2") -> int:
    response = client.post(
        "/api/v1/workspaces",
        headers=owner_headers,
        json={"name": "第二事业部", "code": code},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_viewer_can_read_master_data(client, viewer_headers, bootstrap):
    response = client.get("/api/v1/customers", headers=viewer_headers)
    assert response.status_code == 200
    assert response.json()["total"] == 2


def test_viewer_write_customer_is_403(client, viewer_headers):
    response = client.post(
        "/api/v1/customers",
        headers=viewer_headers,
        json={"code": "NEW-01", "name": "不该成功的客户"},
    )
    assert response.status_code == 403
    body = response.json()["error"]
    assert body["code"] == "PERM_DENIED"
    assert body["details"]["required"] == "customer.manage"
    assert body["details"]["role"] == "VIEWER"


def test_operator_cannot_manage_master_data(client, operator_headers):
    for path, payload in [
        ("/api/v1/customers", {"code": "OP-01", "name": "运营建客户"}),
        ("/api/v1/carriers", {"code": "OP-CR", "name": "运营建承运商"}),
        ("/api/v1/vehicles", {"plate_no": "津A·99999"}),
        ("/api/v1/drivers", {"name": "运营建司机"}),
        ("/api/v1/sla-rules", {"name": "运营建规则"}),
    ]:
        response = client.post(path, headers=operator_headers, json=payload)
        assert response.status_code == 403, f"{path} -> {response.status_code}"
        assert response.json()["error"]["code"] == "PERM_DENIED"


def test_viewer_cannot_patch_or_delete(client, viewer_headers, bootstrap):
    customer_id = bootstrap["customers"]["vip"].id
    patch = client.patch(f"/api/v1/customers/{customer_id}", headers=viewer_headers, json={"name": "改名"})
    delete = client.delete(f"/api/v1/customers/{customer_id}", headers=viewer_headers)
    assert patch.status_code == 403
    assert delete.status_code == 403


def test_admin_can_manage_master_data(client, admin_headers):
    response = client.post(
        "/api/v1/customers",
        headers=admin_headers,
        json={"code": "ADM-01", "name": "管理员建的客户", "level": "VIP", "contact_phone": "13800001234"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["level"] == "VIP"
    assert body["contact_phone"] == "138****1234"  # 脱敏（§8.7）


def test_viewer_cannot_manage_members(client, viewer_headers, bootstrap):
    response = client.post(
        "/api/v1/workspaces/current/members",
        headers=viewer_headers,
        json={"email": OPERATOR, "role": "ADMIN"},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "PERM_DENIED"


def test_cross_tenant_resource_is_404(client, owner_headers, bootstrap):
    other_workspace_id = _new_workspace(client, owner_headers)
    customer_id = bootstrap["customers"]["vip"].id

    headers = dict(owner_headers)
    headers["X-Workspace-Id"] = str(other_workspace_id)
    detail = client.get(f"/api/v1/customers/{customer_id}", headers=headers)
    assert detail.status_code == 404
    assert detail.json()["error"]["code"] == "RESOURCE_NOT_FOUND"

    listing = client.get("/api/v1/customers", headers=headers)
    assert listing.status_code == 200
    assert listing.json()["total"] == 0


def test_non_member_workspace_is_403(client, bootstrap):
    register = client.post(
        "/api/v1/auth/register",
        json={"email": "outsider@logiops.dev", "password": "Outsider@123", "name": "外部人"},
    )
    assert register.status_code == 201
    token = client.post(
        "/api/v1/auth/login", json={"email": "outsider@logiops.dev", "password": "Outsider@123"}
    ).json()["access_token"]

    headers = {"Authorization": f"Bearer {token}", "X-Workspace-Id": str(bootstrap["workspace_id"])}
    response = client.get("/api/v1/customers", headers=headers)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "PERM_WORKSPACE_NOT_MEMBER"


def test_admin_adds_and_removes_member(client, admin_headers, owner_headers, bootstrap):
    register = client.post(
        "/api/v1/auth/register",
        json={"email": "invited@logiops.dev", "password": "Invited@123", "name": "被邀请人"},
    )
    assert register.status_code == 201
    new_user_id = register.json()["user"]["id"]

    added = client.post(
        "/api/v1/workspaces/current/members",
        headers=admin_headers,
        json={"email": "invited@logiops.dev", "role": "OPERATOR"},
    )
    assert added.status_code == 201, added.text
    member = added.json()
    assert member["role"] == "OPERATOR"
    assert member["user_id"] == new_user_id

    duplicate = client.post(
        "/api/v1/workspaces/current/members",
        headers=admin_headers,
        json={"email": "invited@logiops.dev", "role": "VIEWER"},
    )
    assert duplicate.status_code == 409

    promoted = client.patch(
        f"/api/v1/workspaces/current/members/{member['id']}",
        headers=admin_headers,
        json={"role": "ADMIN"},
    )
    assert promoted.status_code == 200, promoted.text
    assert promoted.json()["role"] == "ADMIN"

    removed = client.delete(f"/api/v1/workspaces/current/members/{member['id']}", headers=admin_headers)
    assert removed.status_code == 200
    assert removed.json()["status"] == "REMOVED"
    members = client.get("/api/v1/workspaces/current/members", headers=admin_headers).json()
    assert all(item["user_id"] != new_user_id for item in members)


def test_cannot_remove_self_or_owner(client, admin_headers, bootstrap):
    owner_id = bootstrap["users"]["OWNER"].id
    admin_id = bootstrap["users"]["ADMIN"].id
    members = client.get("/api/v1/workspaces/current/members", headers=admin_headers).json()
    owner_member = next(item for item in members if item["user_id"] == owner_id)
    admin_member = next(item for item in members if item["user_id"] == admin_id)

    remove_owner = client.delete(
        f"/api/v1/workspaces/current/members/{owner_member['id']}", headers=admin_headers
    )
    remove_self = client.delete(
        f"/api/v1/workspaces/current/members/{admin_member['id']}", headers=admin_headers
    )
    assert remove_owner.status_code == 403
    assert remove_self.status_code == 422


def test_member_route_keeps_workspace_scope(client, admin_headers, bootstrap):
    """成员记录 id 与 user_id 两种写法都能解析；跨工作区成员 id 一律 404。"""
    members = client.get("/api/v1/workspaces/current/members", headers=admin_headers).json()
    target = next(item for item in members if item["role"] != "OWNER")
    resolved = client.patch(
        f"/api/v1/workspaces/current/members/{target['user_id']}",
        headers=admin_headers,
        json={"role": target["role"]},
    )
    assert resolved.status_code == 200, resolved.text

    missing = client.delete("/api/v1/workspaces/current/members/999999", headers=admin_headers)
    assert missing.status_code == 404
