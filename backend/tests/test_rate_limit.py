"""AI 分析限流契约（Lead 维护）。

背景：基线文档 §10 / docs/02 承诺「AI 分析 1 次/5 秒 → 429 RATE_LIMITED」，
错误码与前端文案都在，但后端此前**零实现**——是个"幽灵功能"。本文件守住实现。

注：测试环境默认把窗口设为 0（见 conftest），限流用例自行打开窗口并清空状态。
"""

from __future__ import annotations

import pytest

from app.core.config import get_settings
from app.core.errors import HTTP_STATUS, ErrorCode
from app.core.ratelimit import check_rate_limit, reset_rate_limits

# ---------------------------------------------------------------- 单元：滑动窗口


def test_first_call_passes_and_second_waits():
    reset_rate_limits()
    assert check_rate_limit("k", min_interval_seconds=5.0, now=100.0) == 0.0
    wait = check_rate_limit("k", min_interval_seconds=5.0, now=102.0)
    assert 2.9 < wait <= 3.0, f"距上次 2 秒，5 秒窗口应再等 ~3 秒，实际 {wait}"


def test_passes_again_after_window():
    reset_rate_limits()
    assert check_rate_limit("k", min_interval_seconds=5.0, now=100.0) == 0.0
    assert check_rate_limit("k", min_interval_seconds=5.0, now=105.5) == 0.0


def test_zero_window_disables_limit():
    reset_rate_limits()
    assert check_rate_limit("k", min_interval_seconds=0, now=100.0) == 0.0
    assert check_rate_limit("k", min_interval_seconds=0, now=100.1) == 0.0


def test_keys_are_independent():
    reset_rate_limits()
    assert check_rate_limit("user-1", min_interval_seconds=5.0, now=100.0) == 0.0
    assert check_rate_limit("user-2", min_interval_seconds=5.0, now=100.1) == 0.0  # 另一用户不受影响
    assert check_rate_limit("user-1", min_interval_seconds=5.0, now=100.2) > 0


def test_rate_limited_maps_to_429():
    assert HTTP_STATUS[ErrorCode.RATE_LIMITED] == 429


# ------------------------------------------------------- 接口：连续分析 → 429


@pytest.fixture
def analyze_window():
    """为单个用例打开 5 秒限流窗口，并清空既有状态。"""
    settings = get_settings()
    original = settings.rate_limit_ai_analyze_seconds
    settings.rate_limit_ai_analyze_seconds = 5.0
    reset_rate_limits()
    yield
    settings.rate_limit_ai_analyze_seconds = original
    reset_rate_limits()


def _case_confirming(db_session, bootstrap, case_no: str) -> int:
    """建一张"确认中"的异常单，返回 case id。"""
    from app.models import ExceptionCase, Order

    order = Order(
        workspace_id=bootstrap["workspace_id"],
        order_no=f"SO{case_no[2:]}",
        customer_id=bootstrap["customers"]["vip"].id,
        origin_city="天津",
        dest_city="上海",
        status="IN_TRANSIT",
    )
    db_session.add(order)
    db_session.flush()
    case = ExceptionCase(
        workspace_id=bootstrap["workspace_id"],
        case_no=case_no,
        order_id=order.id,
        customer_id=bootstrap["customers"]["vip"].id,
        type="VEHICLE_BREAKDOWN",
        status="CONFIRMING",
    )
    db_session.add(case)
    db_session.commit()
    return case.id


def test_second_analyze_within_window_is_429(client, db_session, bootstrap, admin_headers, analyze_window):
    """第一次分析放行，5 秒内立刻再来一次必须 429 RATE_LIMITED（而不是 500/409）。"""
    case_id = _case_confirming(db_session, bootstrap, "EX202609300777")

    detail = client.get(f"/api/v1/exceptions/{case_id}", headers=admin_headers).json()
    first = client.post(
        f"/api/v1/exceptions/{case_id}/analyze",
        headers=admin_headers,
        json={"expected_version": detail["version"]},
    )
    assert first.status_code in (200, 202), first.text

    # 重新取最新 version（第一次分析会推进状态并自增版本），否则会先撞 409 乐观锁
    fresh = client.get(f"/api/v1/exceptions/{case_id}", headers=admin_headers).json()
    second = client.post(
        f"/api/v1/exceptions/{case_id}/analyze",
        headers=admin_headers,
        json={"expected_version": fresh["version"]},
    )
    assert second.status_code == 429, second.text
    body = second.json()["error"]
    assert body["code"] == "RATE_LIMITED"
    assert body["details"]["retry_after_seconds"] > 0
    assert body["details"]["limit_seconds"] == 5.0


def test_analyze_allowed_when_window_closed(client, db_session, bootstrap, admin_headers):
    """窗口为 0（测试/本地默认）时连续两次分析都不应被限流。"""
    case_id = _case_confirming(db_session, bootstrap, "EX202609300778")
    for _ in range(2):
        detail = client.get(f"/api/v1/exceptions/{case_id}", headers=admin_headers).json()
        response = client.post(
            f"/api/v1/exceptions/{case_id}/analyze",
            headers=admin_headers,
            json={"expected_version": detail["version"]},
        )
        assert response.status_code in (200, 202), response.text
