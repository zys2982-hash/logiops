"""主数据 CRUD 测试（基线文档 §10.3【主数据】、§10.1 分页/排序约定、§10.2 错误码）。

覆盖：增删改查、唯一键 409、乐观锁 409、分页与筛选、排序白名单、软删除语义。
"""

from __future__ import annotations


def _create_customer(client, headers, code: str, name: str, **extra) -> dict:
    payload = {"code": code, "name": name, **extra}
    response = client.post("/api/v1/customers", headers=headers, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


# --- 客户 ---------------------------------------------------------------------
def test_customer_crud_flow(client, admin_headers, bootstrap):
    created = _create_customer(
        client,
        admin_headers,
        "T-01",
        "测试客户",
        level="VIP",
        contact_name="测试联系人",
        contact_phone="13812345678",
        contact_email="test@example.com",
    )
    assert created["level"] == "VIP"
    assert created["contact_phone"] == "138****5678"
    assert created["contact_email"] == "t***@example.com"
    assert created["version"] == 1

    fetched = client.get(f"/api/v1/customers/{created['id']}", headers=admin_headers)
    assert fetched.status_code == 200
    assert fetched.json()["code"] == "T-01"

    updated = client.patch(
        f"/api/v1/customers/{created['id']}",
        headers=admin_headers,
        json={"name": "测试客户改名", "expected_version": 1},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["name"] == "测试客户改名"
    assert updated.json()["version"] == 2

    stale = client.patch(
        f"/api/v1/customers/{created['id']}",
        headers=admin_headers,
        json={"name": "并发改名", "expected_version": 1},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "OPTIMISTIC_LOCK_CONFLICT"

    deleted = client.delete(f"/api/v1/customers/{created['id']}", headers=admin_headers)
    assert deleted.status_code == 200
    assert client.get(f"/api/v1/customers/{created['id']}", headers=admin_headers).status_code == 404
    listing = client.get("/api/v1/customers?q=T-01", headers=admin_headers).json()
    assert listing["total"] == 0


def test_customer_duplicate_code_conflicts(client, admin_headers, bootstrap):
    response = client.post(
        "/api/v1/customers", headers=admin_headers, json={"code": "VIP-01", "name": "重复编码"}
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "DUPLICATE_ENTITY"


def test_customer_not_found(client, admin_headers):
    response = client.get("/api/v1/customers/999999", headers=admin_headers)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "RESOURCE_NOT_FOUND"


def test_customer_pagination_filter_and_sort(client, admin_headers, bootstrap):
    for index in range(3):
        _create_customer(client, admin_headers, f"PG-{index}", f"分页客户{index}", level="NORMAL")
    _create_customer(client, admin_headers, "PG-VIP", "分页VIP客户", level="VIP")

    page = client.get("/api/v1/customers?page=1&page_size=2", headers=admin_headers)
    assert page.status_code == 200
    body = page.json()
    assert set(body) == {"items", "total", "page", "page_size"}
    assert body["total"] == 6 and body["page"] == 1 and body["page_size"] == 2
    assert len(body["items"]) == 2

    second = client.get("/api/v1/customers?page=2&page_size=2", headers=admin_headers).json()
    assert {item["id"] for item in body["items"]}.isdisjoint({item["id"] for item in second["items"]})

    filtered = client.get("/api/v1/customers?q=分页&level=VIP", headers=admin_headers).json()
    assert filtered["total"] == 1
    assert filtered["items"][0]["code"] == "PG-VIP"

    sorted_desc = client.get("/api/v1/customers?sort=-code", headers=admin_headers).json()
    codes = [item["code"] for item in sorted_desc["items"]]
    assert codes == sorted(codes, reverse=True)

    bad_sort = client.get("/api/v1/customers?sort=-unknown_field", headers=admin_headers)
    assert bad_sort.status_code == 422
    assert bad_sort.json()["error"]["code"] == "VALIDATION_ERROR"

    bad_page = client.get("/api/v1/customers?page_size=999", headers=admin_headers)
    assert bad_page.status_code == 422


def test_customer_level_enum_validated(client, admin_headers):
    response = client.post(
        "/api/v1/customers", headers=admin_headers, json={"code": "BAD", "name": "非法等级", "level": "SUPER"}
    )
    assert response.status_code == 422


# --- 承运商 -------------------------------------------------------------------
def test_carrier_crud_and_filter(client, admin_headers, bootstrap):
    created = client.post(
        "/api/v1/carriers",
        headers=admin_headers,
        json={"code": "CR-T", "name": "测试承运商", "contact_phone": "13911112222", "status": "SUSPENDED"},
    )
    assert created.status_code == 201, created.text
    assert created.json()["contact_phone"] == "139****2222"

    duplicate = client.post("/api/v1/carriers", headers=admin_headers, json={"code": "CR-T", "name": "重复"})
    assert duplicate.status_code == 409

    suspended = client.get("/api/v1/carriers?status=SUSPENDED", headers=admin_headers).json()
    assert suspended["total"] == 1

    patched = client.patch(
        f"/api/v1/carriers/{created.json()['id']}", headers=admin_headers, json={"status": "ACTIVE"}
    )
    assert patched.status_code == 200 and patched.json()["status"] == "ACTIVE"


# --- 车辆 ---------------------------------------------------------------------
def test_vehicle_crud_and_relation_checks(client, admin_headers, bootstrap):
    carrier_id = bootstrap["carrier"].id
    # 车与司机 1:1：不能复用 bootstrap 那台车已绑定的司机，先建一名新司机
    driver_created = client.post(
        "/api/v1/drivers",
        headers=admin_headers,
        json={"name": "车辆 CRUD 测试司机", "carrier_id": carrier_id, "status": "AVAILABLE"},
    )
    assert driver_created.status_code == 201, driver_created.text
    driver_id = driver_created.json()["id"]
    created = client.post(
        "/api/v1/vehicles",
        headers=admin_headers,
        json={
            "plate_no": "津A·88888",
            "vehicle_type": "冷藏车",
            "capacity_ton": 15.5,
            "carrier_id": carrier_id,
            "current_driver_id": driver_id,
            "status": "IDLE",
            "current_city": "天津",
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["capacity_ton"] == 15.5
    assert body["carrier_id"] == carrier_id

    by_status = client.get("/api/v1/vehicles?status=IDLE", headers=admin_headers).json()
    assert any(item["id"] == body["id"] for item in by_status["items"])
    by_carrier = client.get(f"/api/v1/vehicles?carrier_id={carrier_id}", headers=admin_headers).json()
    assert by_carrier["total"] >= 1

    duplicate = client.post("/api/v1/vehicles", headers=admin_headers, json={"plate_no": "津A·88888"})
    assert duplicate.status_code == 409

    missing_carrier = client.post(
        "/api/v1/vehicles", headers=admin_headers, json={"plate_no": "津A·77777", "carrier_id": 999999}
    )
    assert missing_carrier.status_code == 404

    patched = client.patch(
        f"/api/v1/vehicles/{body['id']}",
        headers=admin_headers,
        json={"status": "REPAIRING", "current_city": "济南", "expected_version": 1},
    )
    assert patched.status_code == 200
    assert patched.json()["status"] == "REPAIRING"
    assert patched.json()["current_city"] == "济南"


# --- 司机 ---------------------------------------------------------------------
def test_driver_crud_and_filter(client, admin_headers, bootstrap):
    created = client.post(
        "/api/v1/drivers",
        headers=admin_headers,
        json={"name": "测试司机", "phone": "13700009999", "carrier_id": bootstrap["carrier"].id},
    )
    assert created.status_code == 201, created.text
    assert created.json()["phone"] == "137****9999"
    assert created.json()["status"] == "AVAILABLE"

    listed = client.get("/api/v1/drivers?q=测试司机", headers=admin_headers).json()
    assert listed["total"] == 1

    on_duty = client.get("/api/v1/drivers?status=AVAILABLE", headers=admin_headers).json()
    assert on_duty["total"] >= 1

    invalid_status = client.post(
        "/api/v1/drivers", headers=admin_headers, json={"name": "非法状态", "status": "SLEEPING"}
    )
    assert invalid_status.status_code == 422


# --- SLA 规则 -----------------------------------------------------------------
def test_sla_rules_list_create_patch(client, admin_headers, bootstrap):
    listed = client.get("/api/v1/sla-rules", headers=admin_headers)
    assert listed.status_code == 200
    rules = listed.json()
    assert len(rules) == 3
    assert [rule["priority"] for rule in rules] == sorted(rule["priority"] for rule in rules)
    assert rules[0]["scope_type"] == "CUSTOMER"  # VIP-01 专属规则 priority=1 排最前

    created = client.post(
        "/api/v1/sla-rules",
        headers=admin_headers,
        json={
            "name": "SVIP 规则",
            "scope_type": "CUSTOMER_LEVEL",
            "scope_value": "SVIP",
            "deadline_offset_hours": 12,
            "max_delay_minutes": 0,
            "priority": 5,
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["deadline_offset_hours"] == 12

    duplicate = client.post(
        "/api/v1/sla-rules",
        headers=admin_headers,
        json={"name": "重复 SVIP", "scope_type": "CUSTOMER_LEVEL", "scope_value": "SVIP"},
    )
    assert duplicate.status_code == 409

    invalid_scope = client.post(
        "/api/v1/sla-rules",
        headers=admin_headers,
        json={"name": "缺 scope_value", "scope_type": "CUSTOMER_LEVEL"},
    )
    assert invalid_scope.status_code == 422

    # 「指定客户」作用域已下线（用户口径 2026-10-06：只保留「按客户等级 VIP」与「默认」）
    retired_scope = client.post(
        "/api/v1/sla-rules",
        headers=admin_headers,
        json={"name": "VIP-01 专属", "scope_type": "CUSTOMER", "scope_value": "VIP-01"},
    )
    assert retired_scope.status_code == 422, retired_scope.text
    assert retired_scope.json()["error"]["code"] == "VALIDATION_ERROR"

    patched = client.patch(
        f"/api/v1/sla-rules/{created.json()['id']}", headers=admin_headers, json={"max_delay_minutes": 15}
    )
    assert patched.status_code == 200
    assert patched.json()["max_delay_minutes"] == 15

    missing = client.get("/api/v1/sla-rules/999999", headers=admin_headers)
    assert missing.status_code == 404


def test_openapi_schema_is_available(client, bootstrap):
    response = client.get("/openapi.json")
    assert response.status_code == 200, response.text
    spec = response.json()
    assert "/api/v1/customers" in spec["paths"]
    page_schema = spec["paths"]["/api/v1/customers"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    assert "$ref" in page_schema  # 分页对象 {items,total,page,page_size} 已进 OpenAPI


def test_knowledge_doc_detail_returns_chunks(client, db_session, admin_headers, viewer_headers, bootstrap):
    """`GET /knowledge/docs/{id}` 必须能取到分片原文，否则前端知识库页点"来源"恒 404。"""
    from app.models.ops import KnowledgeChunk, KnowledgeDoc

    doc = KnowledgeDoc(
        workspace_id=None,  # 全租户共享
        doc_code="KB-TEST-01",
        title="测试规范",
        category="测试",
        version="v1",
        source_path="kb/test.md",
        status="ACTIVE",
    )
    db_session.add(doc)
    db_session.flush()
    db_session.add_all(
        [
            KnowledgeChunk(doc_id=doc.id, chunk_no=1, section_path="1.1 起", content="第一段原文", token_estimate=10),
            KnowledgeChunk(doc_id=doc.id, chunk_no=2, section_path="1.2 承", content="第二段原文", token_estimate=12),
        ]
    )
    db_session.commit()

    detail = client.get(f"/api/v1/knowledge/docs/{doc.id}", headers=viewer_headers)
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["id"] == doc.id
    assert body["doc_code"] == "KB-TEST-01"
    assert body["title"] == "测试规范"
    assert body["chunk_count"] == 2
    assert [chunk["chunk_no"] for chunk in body["chunks"]] == [1, 2]
    assert body["chunks"][0]["section_path"] == "1.1 起"
    assert body["chunks"][0]["content"] == "第一段原文"
    assert set(body["chunks"][0]) >= {"id", "chunk_no", "section_path", "content", "token_estimate"}

    listing = client.get("/api/v1/knowledge/docs", headers=viewer_headers).json()
    assert any(item["id"] == doc.id and item["chunk_count"] == 2 for item in listing["items"])

    assert client.get("/api/v1/knowledge/docs/999999", headers=viewer_headers).status_code == 404

    # 知识库重建索引：VIEWER 无权（knowledge.manage），ADMIN 可用
    assert client.post("/api/v1/knowledge/reindex", headers=viewer_headers).status_code == 403

    # 跨租户：文档挂到别的工作区后，本工作区 404，对方工作区可见
    other = client.post(
        "/api/v1/workspaces", headers=admin_headers, json={"name": "知识库隔离工作区", "code": "KB-WS"}
    ).json()
    doc.workspace_id = other["id"]
    db_session.add(doc)
    db_session.commit()

    assert client.get(f"/api/v1/knowledge/docs/{doc.id}", headers=admin_headers).status_code == 404
    cross = client.get(
        f"/api/v1/knowledge/docs/{doc.id}",
        headers={**admin_headers, "X-Workspace-Id": str(other["id"])},
    )
    assert cross.status_code == 200


def test_workspace_current_and_list(client, admin_headers, operator_headers, bootstrap):
    current = client.get("/api/v1/workspaces/current", headers=admin_headers)
    assert current.status_code == 200
    assert current.json()["id"] == bootstrap["workspace_id"]
    assert current.json()["role"] == "ADMIN"
    assert current.json()["member_count"] == 4

    listing = client.get("/api/v1/workspaces", headers=operator_headers)
    assert listing.status_code == 200
    assert len(listing.json()) == 1
    assert listing.json()[0]["role"] == "OPERATOR"

    duplicate = client.post(
        "/api/v1/workspaces", headers=admin_headers, json={"name": "重复码", "code": bootstrap["workspace"].code}
    )
    assert duplicate.status_code == 409
