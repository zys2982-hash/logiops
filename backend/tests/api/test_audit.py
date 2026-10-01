"""审计日志测试（基线文档 §10.3【审计】、§9.1 第 6 条、§10.1 分页约定）。

审计是追加写表：所有写操作都要留痕，越权尝试也要留痕。
"""

from __future__ import annotations

from datetime import timedelta

from app.core.clock import now_utc


def test_write_operation_is_audited(client, admin_headers, bootstrap):
    created = client.post(
        "/api/v1/customers", headers=admin_headers, json={"code": "AUD-01", "name": "审计客户"}
    )
    assert created.status_code == 201
    customer_id = created.json()["id"]

    response = client.get(
        "/api/v1/audit-logs", headers=admin_headers, params={"action": "customer.create"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] >= 1
    entry = next(item for item in body["items"] if item["resource_id"] == customer_id)
    assert entry["action"] == "customer.create"
    assert entry["resource_type"] == "customer"
    assert entry["actor_id"] == bootstrap["users"]["ADMIN"].id
    assert entry["actor_type"] == "USER"
    assert entry["source"] == "MANUAL"
    assert entry["after_json"]["code"] == "AUD-01"
    assert entry["occurred_at"].endswith("Z")
    assert entry["request_id"]


def test_audit_filters(client, admin_headers, bootstrap):
    created = client.post(
        "/api/v1/customers", headers=admin_headers, json={"code": "AUD-02", "name": "筛选客户"}
    ).json()
    client.patch(f"/api/v1/customers/{created['id']}", headers=admin_headers, json={"name": "筛选客户改名"})

    by_resource = client.get(
        "/api/v1/audit-logs",
        headers=admin_headers,
        params={"resource_type": "customer", "resource_id": created["id"]},
    ).json()
    assert by_resource["total"] == 2
    actions = {item["action"] for item in by_resource["items"]}
    assert actions == {"customer.create", "customer.update"}

    by_actor = client.get(
        "/api/v1/audit-logs", headers=admin_headers, params={"actor_id": bootstrap["users"]["ADMIN"].id}
    ).json()
    assert by_actor["total"] >= 2

    by_action_like = client.get("/api/v1/audit-logs", headers=admin_headers, params={"action": "customer."}).json()
    assert by_action_like["total"] >= 2

    now = now_utc()
    window = client.get(
        "/api/v1/audit-logs",
        headers=admin_headers,
        params={
            "occurred_from": (now - timedelta(minutes=5)).isoformat(),
            "occurred_to": (now + timedelta(minutes=5)).isoformat(),
        },
    ).json()
    assert window["total"] >= 2

    empty_window = client.get(
        "/api/v1/audit-logs",
        headers=admin_headers,
        params={
            "occurred_from": (now - timedelta(days=30)).isoformat(),
            "occurred_to": (now - timedelta(days=20)).isoformat(),
        },
    ).json()
    assert empty_window["total"] == 0


def test_audit_pagination_and_order(client, admin_headers, bootstrap):
    for index in range(3):
        client.post("/api/v1/customers", headers=admin_headers, json={"code": f"AUD-P{index}", "name": f"分页{index}"})

    page = client.get("/api/v1/audit-logs", headers=admin_headers, params={"page": 1, "page_size": 2})
    assert page.status_code == 200
    body = page.json()
    assert set(body) == {"items", "total", "page", "page_size"}
    assert body["total"] >= 3
    assert len(body["items"]) == 2
    ids = [item["id"] for item in body["items"]]
    assert ids == sorted(ids, reverse=True)  # 倒序


def test_audit_invalid_time_is_422(client, admin_headers):
    response = client.get("/api/v1/audit-logs", headers=admin_headers, params={"occurred_from": "not-a-date"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_viewer_can_read_audit(client, viewer_headers):
    assert client.get("/api/v1/audit-logs", headers=viewer_headers).status_code == 200


def test_denied_permission_is_audited(client, viewer_headers, admin_headers):
    denied = client.post("/api/v1/customers", headers=viewer_headers, json={"code": "NOPE", "name": "越权"})
    assert denied.status_code == 403

    response = client.get(
        "/api/v1/audit-logs", headers=admin_headers, params={"action": "security.permission_denied"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] >= 1
    entry = body["items"][0]
    assert entry["resource_type"] == "permission"
    assert entry["after_json"]["required"] == "customer.manage"
    assert entry["source"] == "SYSTEM"


def test_cross_tenant_denial_is_audited(client, admin_headers, owner_headers, bootstrap):
    """跨租户尝试要留痕：审计写在"用户自己的工作区"，被尝试的租户放在 after（§9.1 第 6 条）。"""
    response = client.get("/api/v1/customers", headers={**admin_headers, "X-Workspace-Id": "987654"})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "PERM_WORKSPACE_NOT_MEMBER"

    logs = client.get(
        "/api/v1/audit-logs", headers=owner_headers, params={"action": "security.cross_tenant_denied"}
    ).json()
    assert logs["total"] >= 1
    entry = logs["items"][0]
    assert entry["workspace_id"] == bootstrap["workspace_id"]
    assert entry["resource_type"] == "workspace"
    assert entry["actor_id"] == bootstrap["users"]["ADMIN"].id
    assert entry["after_json"]["attempted_workspace_id"] == 987654
    assert entry["source"] == "SYSTEM"


def test_audit_is_workspace_scoped(client, owner_headers, admin_headers, bootstrap):
    client.post("/api/v1/customers", headers=admin_headers, json={"code": "AUD-03", "name": "工作区隔离"})

    created = client.post(
        "/api/v1/workspaces", headers=owner_headers, json={"name": "另一工作区", "code": "AUD-WS2"}
    )
    assert created.status_code == 201
    other_id = created.json()["id"]

    other = client.get(
        "/api/v1/audit-logs", headers={**owner_headers, "X-Workspace-Id": str(other_id)}
    ).json()
    # 新工作区只能看到自己的 workspace.create，看不到主工作区的 customer.create
    assert other["total"] == 1
    assert other["items"][0]["action"] == "workspace.create"
    assert other["items"][0]["workspace_id"] == other_id

    primary = client.get("/api/v1/audit-logs", headers=owner_headers, params={"action": "customer."}).json()
    assert primary["total"] >= 1
    assert all(item["workspace_id"] == bootstrap["workspace_id"] for item in primary["items"])
