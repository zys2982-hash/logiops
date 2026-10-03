"""异常接口（§10.3 /exceptions）：自动建单、确认/分析/消息闭环、状态机 409、乐观锁 409、VIEWER 403、跨租户 404。"""

from __future__ import annotations

from app.models.auth import Workspace
from app.models.enums import ExceptionStatus
from app.models.exception import ExceptionCase
from app.models.master import Customer
from app.repositories import Repos
from app.services.exceptions import ExceptionService
from tests.unit import _support

EXCEPTIONS = "/api/v1/exceptions"
ORDERS = "/api/v1/orders"


def _dispatched_order(client, headers, bootstrap, *, distance_km: int = 200, admin_headers=None) -> dict:
    """订单创建/基础信息 = ADMIN+（§9.2），派车 = OPERATOR+（§8.1）。"""
    write_headers = admin_headers or headers
    created = client.post(
        ORDERS,
        headers=write_headers,
        json={
            "customer_id": bootstrap["customers"]["vip"].id,
            "origin_city": "天津",
            "dest_city": "上海",
            "distance_km": distance_km,
        },
    ).json()
    client.patch(
        f"{ORDERS}/{created['id']}",
        headers=headers,
        json={"carrier_id": bootstrap["carrier"].id, "vehicle_id": bootstrap["vehicle"].id},
    )
    return client.get(f"{ORDERS}/{created['id']}", headers=headers).json()


def _auto_exception(client, headers, bootstrap, *, admin_headers=None) -> dict:
    """建单 → 派车 → DEPART → 推进 130 分钟 → 自动命中停滞规则。"""
    order = _dispatched_order(client, headers, bootstrap, admin_headers=admin_headers)
    client.post(
        f"{ORDERS}/{order['id']}/tracking-events",
        headers=headers,
        json={"event_type": "DEPART", "city": "天津", "source": "OPERATOR"},
    )
    tick = client.post("/api/v1/demo/actions/tick", headers=headers, json={"minutes": 130})
    assert tick.status_code == 200, tick.text
    listed = client.get(EXCEPTIONS, headers=headers, params={"status": "DETECTED"})
    assert listed.status_code == 200, listed.text
    items = listed.json()["items"]
    assert items, "推进时钟后应自动建单"
    case = next((item for item in items if item["order_id"] == order["id"]), None)
    assert case is not None, listed.json()
    return case


def _other_tenant_exception(db_session, bootstrap) -> ExceptionCase:
    workspace = Workspace(name="其他租户2", code="OT2", owner_user_id=bootstrap["users"]["OWNER"].id)
    db_session.add(workspace)
    db_session.flush()
    customer = Customer(workspace_id=workspace.id, code="OT2-01", name="其他客户2", level="NORMAL")
    db_session.add(customer)
    db_session.flush()
    from app.models.transport import Order

    order = Order(
        workspace_id=workspace.id,
        order_no="SO-OT2-0001",
        customer_id=customer.id,
        origin_city="北京",
        dest_city="广州",
        status="IN_TRANSIT",
    )
    db_session.add(order)
    db_session.flush()
    case = ExceptionCase(
        workspace_id=workspace.id,
        case_no="EX-OT2-0001",
        order_id=order.id,
        customer_id=customer.id,
        type="DELAY_RISK",
        level="LOW",
        status="DETECTED",
    )
    db_session.add(case)
    db_session.commit()
    return case


def test_auto_detection_creates_case_with_rule_level(client, bootstrap, operator_headers, admin_headers):
    case = _auto_exception(client, operator_headers, bootstrap, admin_headers=admin_headers)
    assert case["status"] == "DETECTED"
    assert case["detected_by"] == "SYSTEM"
    assert case["detection_rule"] == "STALL_OVER_THRESHOLD"
    assert case["type"] == "VEHICLE_BREAKDOWN"
    assert case["level"] in {"MEDIUM", "HIGH", "CRITICAL"}
    assert case["risk_score"] is not None
    assert case["risk_factors"]
    assert case["order_no"]
    assert case["sla_delay_minutes"] is not None


def test_confirm_analyze_and_messages_flow(client, bootstrap, operator_headers, admin_headers):
    case = _auto_exception(client, operator_headers, bootstrap, admin_headers=admin_headers)
    case_id = case["id"]

    confirmed = client.post(f"{EXCEPTIONS}/{case_id}/confirm", headers=operator_headers)
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "PROCESSING"

    # 重复确认 → 409 状态机冲突
    conflict = client.post(f"{EXCEPTIONS}/{case_id}/confirm", headers=operator_headers)
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "STATE_TRANSITION_INVALID"

    # 触发 AI 分析：新建 202（AI 层缺失时也不能 500，只是 FAILED）
    analyzed = client.post(f"{EXCEPTIONS}/{case_id}/analyze", headers=operator_headers)
    assert analyzed.status_code in {200, 202}, analyzed.text
    payload = analyzed.json()
    assert payload["analysis_id"]
    assert payload["status"] in {"PENDING", "RUNNING", "READY", "FAILED"}

    detail = client.get(f"/api/v1/ai-analyses/{payload['analysis_id']}", headers=operator_headers)
    assert detail.status_code == 200
    assert detail.json()["status"] in {"PENDING", "RUNNING", "READY", "FAILED"}
    assert isinstance(detail.json()["steps"], list)

    # 录入承运商消息 → 同步解析（AI 不可用时标 FAILED，但接口必须成功）
    message = client.post(
        f"{EXCEPTIONS}/{case_id}/messages",
        headers=operator_headers,
        json={"raw_text": "车在济南爆胎了，预计晚上 8 点恢复", "channel": "MANUAL_PASTE"},
    )
    assert message.status_code == 201, message.text
    assert message.json()["message_id"]
    assert message.json()["parse_status"] in {"PARSED", "FAILED"}

    messages = client.get(f"{EXCEPTIONS}/{case_id}/messages", headers=operator_headers)
    assert messages.status_code == 200
    assert len(messages.json()) == 1
    assert messages.json()[0]["raw_text"].startswith("车在济南爆胎了")

    timeline = client.get(f"{EXCEPTIONS}/{case_id}/events", headers=operator_headers)
    assert timeline.status_code == 200
    events = timeline.json()
    assert events["total"] >= 2
    event_types = {item["event_type"] for item in timeline.json()["items"]}
    assert {"DETECTED", "CONFIRMED", "ANALYSIS_REQUESTED", "MESSAGE_ADDED"} <= event_types


def test_exception_detail_shape(client, bootstrap, operator_headers, admin_headers):
    case = _auto_exception(client, operator_headers, bootstrap, admin_headers=admin_headers)
    detail = client.get(f"{EXCEPTIONS}/{case['id']}", headers=operator_headers)
    assert detail.status_code == 200
    body = detail.json()
    for key in (
        "level",
        "risk_score",
        "risk_factors",
        "sla_delay_minutes",
        "sla_breached",
        "status",
        "closed_at",
        "version",
        "order",
        "customer",
        "vehicle",
        "sla",
        "latest_analysis",
        "counts",
    ):
        assert key in body, key
    assert body["order"]["current_eta_at"] is not None
    assert body["customer"]["contact_phone"] == "138****0001"  # 手机号脱敏
    assert body["counts"]["messages"] == 0


def test_patch_exception_requires_version_and_checks_it(client, bootstrap, operator_headers, admin_headers):
    case = _auto_exception(client, operator_headers, bootstrap, admin_headers=admin_headers)
    case_id = case["id"]

    missing = client.patch(f"{EXCEPTIONS}/{case_id}", headers=operator_headers, json={"remark": "x"})
    assert missing.status_code == 422

    ok = client.patch(
        f"{EXCEPTIONS}/{case_id}",
        headers=operator_headers,
        json={"expected_version": case["version"], "remark": "已联系承运商"},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["version"] == case["version"] + 1

    stale = client.patch(
        f"{EXCEPTIONS}/{case_id}",
        headers=operator_headers,
        json={"expected_version": case["version"], "remark": "并发"},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "OPTIMISTIC_LOCK_CONFLICT"


def test_manual_create_requires_admin_for_level_and_detects_duplicate(
    client, bootstrap, operator_headers, admin_headers
):
    order = _dispatched_order(client, operator_headers, bootstrap, admin_headers=admin_headers)
    body = {
        "order_id": order["id"],
        "type": "DELAY_RISK",
        "occurred_at": "2026-09-30T01:00:00Z",
        "note": "客户临时改地址，预计延误",
    }

    forbidden = client.post(EXCEPTIONS, headers=operator_headers, json={**body, "level": "HIGH"})
    assert forbidden.status_code == 403
    assert forbidden.json()["error"]["code"] == "PERM_DENIED"

    created = client.post(EXCEPTIONS, headers=operator_headers, json=body)
    assert created.status_code == 201, created.text
    assert created.json()["detected_by"] == "OPERATOR"
    assert created.json()["detection_rule"] == "MANUAL"

    duplicate = client.post(EXCEPTIONS, headers=operator_headers, json=body)
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "OPEN_EXCEPTION_EXISTS"
    assert duplicate.json()["error"]["details"]["exception_id"] == created.json()["id"]

    # 换一张订单，验证 ADMIN+ 可以手工指定等级
    other_order = _dispatched_order(client, operator_headers, bootstrap, admin_headers=admin_headers)
    with_level = client.post(
        EXCEPTIONS, headers=admin_headers, json={**body, "order_id": other_order["id"], "level": "CRITICAL"}
    )
    assert with_level.status_code == 201, with_level.text
    assert with_level.json()["level"] == "CRITICAL"


def test_search_by_q_hits_order_no_and_case_no(client, bootstrap, operator_headers, admin_headers):
    """?q 必须能按订单号/单号搜索（前端异常中心搜索框）。"""
    case = _auto_exception(client, operator_headers, bootstrap, admin_headers=admin_headers)
    order = client.get(f"{ORDERS}/{case['order_id']}", headers=operator_headers).json()

    assert client.get(EXCEPTIONS, headers=operator_headers).status_code == 200

    by_order = client.get(EXCEPTIONS, headers=operator_headers, params={"q": order["order_no"]})
    assert by_order.status_code == 200, by_order.text
    assert by_order.json()["total"] >= 1, by_order.json()
    assert any(item["id"] == case["id"] for item in by_order.json()["items"])

    by_case = client.get(EXCEPTIONS, headers=operator_headers, params={"q": case["case_no"]})
    assert by_case.status_code == 200
    assert any(item["id"] == case["id"] for item in by_case.json()["items"])

    by_alias = client.get(EXCEPTIONS, headers=operator_headers, params={"keyword": order["order_no"]})
    assert by_alias.json()["total"] >= 1

    miss = client.get(EXCEPTIONS, headers=operator_headers, params={"q": "不存在的单号ZZZ"})
    assert miss.status_code == 200
    assert miss.json()["total"] == 0


def test_message_parse_result_keeps_structured_fields(
    client, bootstrap, operator_headers, admin_headers, monkeypatch
):
    """§11.2 表 2：parse_result 必须落结构化字段（AI 可用与不可用两条路径）。"""
    from app.services import ai_bridge

    case = _auto_exception(client, operator_headers, bootstrap, admin_headers=admin_headers)
    case_id = case["id"]

    def fake_parse(session, repos, message_id):
        return {
            "ok": True,
            "result": {
                "status": "PARSED",
                "output": {
                    "exception_type": "VEHICLE_BREAKDOWN",
                    "location": "济南",
                    "status": "REPAIRING",
                    "estimated_recovery_at": "2026-09-30T12:00:00Z",
                    "confidence": 0.82,
                    "missing_info": [],
                },
                "model": "deepseek-chat",
                "is_replay": True,
                "prompt_version": "v1",
            },
        }

    monkeypatch.setattr(ai_bridge, "parse_message", fake_parse)
    parsed_response = client.post(
        f"{EXCEPTIONS}/{case_id}/messages",
        headers=operator_headers,
        json={"raw_text": "车在济南爆胎了，预计晚上 8 点恢复"},
    )
    assert parsed_response.status_code == 201, parsed_response.text
    body = parsed_response.json()
    assert body["parse_status"] == "PARSED"
    parsed = body["parse_result"]
    assert parsed["location"] == "济南"
    assert parsed["status"] == "REPAIRING"
    assert parsed["exception_type"] == "VEHICLE_BREAKDOWN"
    assert parsed["confidence"] == 0.82
    assert parsed["estimated_recovery_at"] == "2026-09-30T12:00:00Z"
    assert "missing_info" in parsed
    assert parsed["meta"]["prompt_version"] == "v1"

    def dead_parse(session, repos, message_id):
        return {"ok": False, "error_code": "LLM_UNAVAILABLE", "error_message": "AI 不可用"}

    monkeypatch.setattr(ai_bridge, "parse_message", dead_parse)
    fallback_response = client.post(
        f"{EXCEPTIONS}/{case_id}/messages",
        headers=operator_headers,
        json={"raw_text": "车在济南爆胎了，预计晚上 8 点恢复"},
    )
    assert fallback_response.status_code == 201, fallback_response.text
    fallback_body = fallback_response.json()
    assert fallback_body["parse_status"] == "FAILED"
    fallback = fallback_body["parse_result"]
    assert fallback["status"] == "REPAIRING"  # 关键词兜底
    assert fallback["estimated_recovery_at"]  # 从"晚上 8 点"推导
    assert fallback["location"]

    listed = client.get(f"{EXCEPTIONS}/{case_id}/messages", headers=operator_headers).json()
    assert len(listed) == 2
    assert listed[0]["parse_result"]["location"] == "济南"
    assert listed[1]["parse_error"]


def test_invalid_close_from_detected_is_terminal(client, bootstrap, operator_headers, admin_headers):
    """CASE-C 误报：DETECTED 阶段可直接判 INVALID 关闭，且终态不可逆。"""
    case = _auto_exception(client, operator_headers, bootstrap, admin_headers=admin_headers)
    case_id = case["id"]

    # DETECTED 阶段不能 resolve（状态机不允许）
    early = client.post(
        f"{EXCEPTIONS}/{case_id}/resolve",
        headers=operator_headers,
        json={"expected_version": case["version"], "note": "先解决试试"},
    )
    assert early.status_code == 409

    closed = client.post(
        f"{EXCEPTIONS}/{case_id}/close",
        headers=operator_headers,
        json={"expected_version": case["version"], "reason_code": "INVALID", "note": "实为装卸排队"},
    )
    assert closed.status_code == 200, closed.text
    assert closed.json()["status"] == "CLOSED"
    assert closed.json()["close_reason"] == "INVALID"
    assert closed.json()["closed_at"] is not None

    # 终态不可逆
    again = client.post(
        f"{EXCEPTIONS}/{case_id}/close",
        headers=operator_headers,
        json={"expected_version": closed.json()["version"], "reason_code": "MANUAL", "note": "再来一次"},
    )
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "STATE_TRANSITION_INVALID"


def test_resolve_then_close_from_processing(client, db_session, bootstrap, operator_headers):
    """PROCESSING → RESOLVED（必填 note）→ CLOSED。"""
    repos = Repos(db_session, workspace_id=bootstrap["workspace_id"])
    case = _support.detected_exception(repos, bootstrap)
    service = ExceptionService(repos)
    service.confirm(case.id, expected_version=case.version, actor_id=None)
    analysis = _support.make_analysis(repos, case, output=_support.ai_output())
    service.apply_analysis_result(analysis.id)
    db_session.commit()
    assert case.status == str(ExceptionStatus.PROCESSING)

    resolved = client.post(
        f"{EXCEPTIONS}/{case.id}/resolve",
        headers=operator_headers,
        json={"expected_version": case.version, "note": "车辆已恢复，风险解除"},
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["status"] == "RESOLVED"
    assert resolved.json()["resolved_at"] is not None

    closed = client.post(
        f"{EXCEPTIONS}/{case.id}/close",
        headers=operator_headers,
        json={"expected_version": resolved.json()["version"], "reason_code": "MANUAL", "note": "归档"},
    )
    assert closed.status_code == 200, closed.text
    assert closed.json()["status"] == "CLOSED"
    assert closed.json()["close_reason"] == "MANUAL"


def test_close_requires_expected_version_and_note_for_force(client, bootstrap, operator_headers, admin_headers):
    case = _auto_exception(client, operator_headers, bootstrap, admin_headers=admin_headers)
    case_id = case["id"]
    client.post(f"{EXCEPTIONS}/{case_id}/confirm", headers=operator_headers)

    missing_version = client.post(
        f"{EXCEPTIONS}/{case_id}/close", headers=operator_headers, json={"reason_code": "MANUAL"}
    )
    assert missing_version.status_code == 422

    current = client.get(f"{EXCEPTIONS}/{case_id}", headers=operator_headers).json()
    # CONFIRMING → CLOSED 需要强制关闭权限（ADMIN+）
    operator_try = client.post(
        f"{EXCEPTIONS}/{case_id}/close",
        headers=operator_headers,
        json={"expected_version": current["version"], "reason_code": "FORCED_CLOSE", "note": "运营强行归档"},
    )
    assert operator_try.status_code == 409

    admin_no_note = client.post(
        f"{EXCEPTIONS}/{case_id}/close",
        headers=admin_headers,
        json={"expected_version": current["version"], "reason_code": "FORCED_CLOSE"},
    )
    assert admin_no_note.status_code == 422

    admin_ok = client.post(
        f"{EXCEPTIONS}/{case_id}/close",
        headers=admin_headers,
        json={"expected_version": current["version"], "reason_code": "FORCED_CLOSE", "note": "管理员强制归档"},
    )
    assert admin_ok.status_code == 200, admin_ok.text
    assert admin_ok.json()["close_reason"] == "FORCED_CLOSE"


def test_viewer_is_read_only(client, bootstrap, operator_headers, viewer_headers, admin_headers):
    case = _auto_exception(client, operator_headers, bootstrap, admin_headers=admin_headers)
    case_id = case["id"]
    assert client.get(EXCEPTIONS, headers=viewer_headers).status_code == 200
    assert client.get(f"{EXCEPTIONS}/{case_id}", headers=viewer_headers).status_code == 200
    assert client.post(f"{EXCEPTIONS}/{case_id}/confirm", headers=viewer_headers).status_code == 403
    assert client.post(f"{EXCEPTIONS}/{case_id}/analyze", headers=viewer_headers).status_code == 403
    assert (
        client.post(
            f"{EXCEPTIONS}/{case_id}/messages",
            headers=viewer_headers,
            json={"raw_text": "x"},
        ).status_code
        == 403
    )
    assert (
        client.post(
            EXCEPTIONS,
            headers=viewer_headers,
            json={
                "order_id": case["order_id"],
                "type": "DELAY_RISK",
                "occurred_at": "2026-09-30T01:00:00Z",
                "note": "x",
            },
        ).status_code
        == 403
    )


def test_cross_tenant_exception_returns_404(client, db_session, bootstrap, operator_headers):
    other = _other_tenant_exception(db_session, bootstrap)
    assert client.get(f"{EXCEPTIONS}/{other.id}", headers=operator_headers).status_code == 404
    assert client.get(f"{EXCEPTIONS}/{other.id}/events", headers=operator_headers).status_code == 404
    assert client.post(f"{EXCEPTIONS}/{other.id}/confirm", headers=operator_headers).status_code == 404
