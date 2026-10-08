"""修改密码接口测试（POST /api/v1/auth/password）。

覆盖：改成功 + 新密码可登录、原密码错、强度校验（长度/字母+数字/与原相同）、
未登录、只改自己（不影响他人）、审计留痕。
"""

from __future__ import annotations

from sqlalchemy import select

from app.models import AuditLog

OPERATOR = "operator@logiops.dev"
OWNER = "owner@logiops.dev"
NEW_PASSWORD = "NewPass@2026"


def _login(client, email: str, password: str):
    return client.post("/api/v1/auth/login", json={"email": email, "password": password})


def _change(client, headers, old: str, new: str):
    return client.post(
        "/api/v1/auth/password",
        json={"old_password": old, "new_password": new},
        headers=headers,
    )


def test_change_password_success_then_login_with_new(client, bootstrap, operator_headers):
    old_password = bootstrap["password"]
    response = _change(client, operator_headers, old_password, NEW_PASSWORD)
    assert response.status_code == 200, response.text
    assert response.json()["ok"] is True

    # 新密码可登录
    assert _login(client, OPERATOR, NEW_PASSWORD).status_code == 200
    # 旧密码失效
    failed = _login(client, OPERATOR, old_password)
    assert failed.status_code == 401
    assert failed.json()["error"]["code"] == "AUTH_INVALID_CREDENTIALS"


def test_change_password_wrong_old_password_is_422_not_401(client, bootstrap, operator_headers):
    """必须是 422、不能是 401：前端拦截器把任何 401 当会话失效并登出（stores/auth.ts）。"""
    response = _change(client, operator_headers, "not-my-password", NEW_PASSWORD)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert response.json()["error"]["details"]["field"] == "old_password"
    # 原密码仍然有效（不能因为一次失败尝试就把账号锁住）
    assert _login(client, OPERATOR, bootstrap["password"]).status_code == 200


def test_change_password_requires_token(client, bootstrap):
    response = _change(client, {}, bootstrap["password"], NEW_PASSWORD)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_TOKEN_MISSING"


def test_change_password_too_short_is_422(client, bootstrap, operator_headers):
    response = _change(client, operator_headers, bootstrap["password"], "Ab1@567")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_change_password_without_digit_is_422(client, bootstrap, operator_headers):
    response = _change(client, operator_headers, bootstrap["password"], "onlyletters")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_change_password_without_letter_is_422(client, bootstrap, operator_headers):
    response = _change(client, operator_headers, bootstrap["password"], "1234567890")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_change_password_same_as_old_is_422(client, bootstrap, operator_headers):
    response = _change(client, operator_headers, bootstrap["password"], bootstrap["password"])
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_change_password_only_affects_self(client, bootstrap, operator_headers, owner_headers):
    """接口从 token 取目标用户：改 operator 的密码绝不能动到 owner。"""
    assert _change(client, operator_headers, bootstrap["password"], NEW_PASSWORD).status_code == 200
    # operator 新密码可登录
    assert _login(client, OPERATOR, NEW_PASSWORD).status_code == 200
    # owner 完全不受影响
    assert _login(client, OWNER, bootstrap["password"]).status_code == 200
    assert _change(client, owner_headers, bootstrap["password"], "OwnerPass@2026").status_code == 200


def test_change_password_writes_audit_without_password_content(client, bootstrap, db_session, operator_headers):
    operator_id = bootstrap["users"]["OPERATOR"].id
    assert _change(client, operator_headers, bootstrap["password"], NEW_PASSWORD).status_code == 200

    rows = db_session.scalars(
        select(AuditLog).where(
            AuditLog.action == "auth.password_changed",
            AuditLog.resource_id == operator_id,
        )
    ).all()
    assert len(rows) == 1
    entry = rows[0]
    assert entry.actor_id == operator_id
    assert entry.workspace_id == bootstrap["workspace_id"]
    assert entry.after_json == {"email": OPERATOR}
    # 审计里绝不能出现任何密码明文或哈希
    serialized = f"{entry.before_json}{entry.after_json}"
    assert NEW_PASSWORD not in serialized
    assert bootstrap["password"] not in serialized


def test_password_hash_actually_changed_in_db(client, bootstrap, db_session, operator_headers):
    """防"接口返回 200 但没落库"：直接核对库里的哈希变了。"""
    operator = bootstrap["users"]["OPERATOR"]
    before = operator.password_hash
    assert _change(client, operator_headers, bootstrap["password"], NEW_PASSWORD).status_code == 200

    db_session.expire_all()
    refreshed = db_session.get(type(operator), operator.id)
    assert refreshed is not None
    assert refreshed.password_hash != before
    assert NEW_PASSWORD not in refreshed.password_hash  # 存的是哈希，不是明文
