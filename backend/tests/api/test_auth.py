"""认证接口测试（基线文档 §10.3【认证】、§10.2 错误码）。

覆盖：登录成功/失败、注册、/me 的角色与权限列表、登出、无/坏 token。
"""

from __future__ import annotations

OPERATOR = "operator@logiops.dev"
OWNER = "owner@logiops.dev"
VIEWER = "viewer@logiops.dev"
NEW_USER = {"email": "newbie@logiops.dev", "password": "Newbie@12345", "name": "新人"}


def login(client, email: str, password: str) -> dict:
    response = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()


def test_login_success_returns_token_and_user(client, bootstrap):
    body = login(client, OPERATOR, bootstrap["password"])
    assert body["token_type"] == "bearer"
    assert body["expires_in"] > 0
    assert body["access_token"]
    assert body["user"]["email"] == OPERATOR
    assert body["user"]["name"] == "张三"
    assert body["user"]["created_at"].endswith("Z")


def test_login_wrong_password(client, bootstrap):
    response = client.post("/api/v1/auth/login", json={"email": OPERATOR, "password": "wrong-password"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_INVALID_CREDENTIALS"


def test_login_unknown_email(client, bootstrap):
    response = client.post("/api/v1/auth/login", json={"email": "nobody@logiops.dev", "password": "Demo@12345"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_INVALID_CREDENTIALS"


def test_login_email_is_case_insensitive(client, bootstrap):
    body = login(client, OPERATOR.upper(), bootstrap["password"])
    assert body["user"]["email"] == OPERATOR


def test_login_updates_last_login_at(client, db_session, bootstrap):
    body = login(client, OWNER, bootstrap["password"])
    assert body["user"]["last_login_at"] is not None


def test_me_requires_token(client, bootstrap):
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_TOKEN_MISSING"


def test_me_rejects_invalid_token(client, bootstrap):
    response = client.get("/api/v1/auth/me", headers={"Authorization": "Bearer not-a-real-token"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_TOKEN_INVALID"


def test_me_returns_role_and_permissions(client, operator_headers):
    response = client.get("/api/v1/auth/me", headers=operator_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["role"] == "OPERATOR"
    assert body["workspace"] is not None
    assert body["workspace_id"] == body["workspace"]["id"]
    assert len(body["workspaces"]) == 1
    assert "order.view" in body["permissions"]
    assert "exception.handle" in body["permissions"]
    assert "customer.manage" not in body["permissions"]
    assert body["permissions"] == sorted(body["permissions"])


def test_me_permissions_follow_role_matrix(client, viewer_headers, owner_headers):
    viewer = client.get("/api/v1/auth/me", headers=viewer_headers).json()
    owner = client.get("/api/v1/auth/me", headers=owner_headers).json()
    assert "customer.view" in viewer["permissions"]
    assert "customer.manage" not in viewer["permissions"]
    assert set(viewer["permissions"]) < set(owner["permissions"])
    assert "workspace.delete" in owner["permissions"]


def test_me_with_foreign_workspace_header_is_403(client, owner_headers):
    headers = dict(owner_headers)
    headers["X-Workspace-Id"] = "999999"
    response = client.get("/api/v1/auth/me", headers=headers)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "PERM_WORKSPACE_NOT_MEMBER"


def test_logout(client, operator_headers):
    response = client.post("/api/v1/auth/logout", headers=operator_headers)
    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_register_creates_user_and_default_workspace(client):
    response = client.post("/api/v1/auth/register", json=NEW_USER)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["user"]["email"] == NEW_USER["email"]
    assert body["user"]["status"] == "ACTIVE"
    assert body["default_workspace_id"]

    token = login(client, NEW_USER["email"], NEW_USER["password"])["access_token"]
    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}).json()
    assert me["role"] == "OWNER"
    assert me["workspace_id"] == body["default_workspace_id"]
    assert "member.manage" in me["permissions"]


def test_register_duplicate_email_conflicts(client, bootstrap):
    payload = {"email": OWNER, "password": "Another@12345", "name": "重名"}
    response = client.post("/api/v1/auth/register", json=payload)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "DUPLICATE_ENTITY"


def test_register_invalid_email_is_422(client):
    response = client.post(
        "/api/v1/auth/register", json={"email": "bad-email", "password": "Another@12345", "name": "错"}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_register_short_password_is_422(client):
    response = client.post(
        "/api/v1/auth/register", json={"email": "short@logiops.dev", "password": "123", "name": "短"}
    )
    assert response.status_code == 422
