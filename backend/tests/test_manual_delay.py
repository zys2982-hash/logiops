"""人工延误（①②已实现）的接口契约：录入即由规则重判违约（Lead 维护）。

口径（docs/08）：人报事实（延误分钟），是否违约仍由 SLA 规则判定。
CASE-A 客户 VIP-01 的规则是「24h 承诺偏移 / 允许延迟 0 分钟」，所以：
  延误 45 → 违约；延误 0 → 不违约（规则用 > 而非 >=）。
"""

from __future__ import annotations

from sqlalchemy import select

from app.models import AuditLog
from app.seed import reset_demo_data


def _case_a(client, headers) -> dict:
    response = client.get("/api/v1/exceptions?q=EX20260930001&page_size=1", headers=headers)
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert items, "CASE-A 异常不存在（seed 未生效？）"
    return items[0]


def _seed(db_session, bootstrap) -> None:
    reset_demo_data(db_session, workspace_id=bootstrap["workspace_id"], with_knowledge=False)
    db_session.commit()


def test_record_delay_drives_rule_judgement(client, db_session, bootstrap, admin_headers):
    """录延误 → 规则重判：45 分钟违约（允许 0），0 分钟不违约。"""
    _seed(db_session, bootstrap)
    case = _case_a(client, admin_headers)

    first = client.post(
        f"/api/v1/exceptions/{case['id']}/delay",
        headers=admin_headers,
        json={"expected_version": case["version"], "delay_minutes": 45, "note": "人工：晚了 45 分钟"},
    )
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["delay_minutes"] == 45
    assert body["sla_delay_minutes"] == 45
    assert body["sla_breached"] is True, "VIP-01 允许延迟 0 分钟，延误 45 分钟必须判违约"

    second = client.post(
        f"/api/v1/exceptions/{case['id']}/delay",
        headers=admin_headers,
        json={"expected_version": body["version"], "delay_minutes": 0, "note": "人工：其实准点"},
    )
    assert second.status_code == 200, second.text
    assert second.json()["sla_breached"] is False, "延误 0 分钟不能判违约（规则用 > 而不是 >=）"


def test_record_delay_keeps_expected_eta_consistent(client, db_session, bootstrap, admin_headers):
    """人工延误反推 expected_eta_at = 承诺 + 延误（这样 AI/guard 读到的字段仍自洽）。"""
    _seed(db_session, bootstrap)
    case = _case_a(client, admin_headers)
    promised = case["promised_delivery_at"]
    assert promised, "CASE-A 应有承诺到达时间"

    response = client.post(
        f"/api/v1/exceptions/{case['id']}/delay",
        headers=admin_headers,
        json={"expected_version": case["version"], "delay_minutes": 30},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["promised_delivery_at"] == promised
    assert body["expected_eta_at"] > promised, "预计送达（反推）= 承诺 + 延误，必须晚于承诺"


def test_record_delay_rejects_stale_version(client, db_session, bootstrap, admin_headers):
    """乐观锁：过期 version → 409，不静默覆盖。"""
    _seed(db_session, bootstrap)
    case = _case_a(client, admin_headers)
    response = client.post(
        f"/api/v1/exceptions/{case['id']}/delay",
        headers=admin_headers,
        json={"expected_version": 999_999, "delay_minutes": 10},
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "OPTIMISTIC_LOCK_CONFLICT"


def test_record_delay_rejects_negative_minutes(client, db_session, bootstrap, admin_headers):
    """负延误不允许（提前请填 0）→ 422 字段级。"""
    _seed(db_session, bootstrap)
    case = _case_a(client, admin_headers)
    response = client.post(
        f"/api/v1/exceptions/{case['id']}/delay",
        headers=admin_headers,
        json={"expected_version": case["version"], "delay_minutes": -5},
    )
    assert response.status_code == 422, response.text


def test_record_delay_writes_audit(client, db_session, bootstrap, admin_headers):
    """人工改判定必须留痕：审计里要有 exception.delay_recorded 及 before/after。"""
    _seed(db_session, bootstrap)
    case = _case_a(client, admin_headers)
    response = client.post(
        f"/api/v1/exceptions/{case['id']}/delay",
        headers=admin_headers,
        json={"expected_version": case["version"], "delay_minutes": 90, "note": "人工：晚了 1.5 小时"},
    )
    assert response.status_code == 200, response.text

    rows = db_session.scalars(
        select(AuditLog).where(
            AuditLog.action == "exception.delay_recorded",
            AuditLog.resource_id == case["id"],
        )
    ).all()
    assert rows, "缺少 exception.delay_recorded 审计"
    after = rows[-1].after_json or {}
    assert after.get("delay_minutes") == 90
    assert after.get("sla_breached") is True
