"""时钟模式运行时切换：真实时间（system）↔ 虚拟时钟（replay）。

与「切换 AI 模式」同一套机制：改的是进程内 `get_settings()` 单例，立即生效、不写库；
重启后回到 `.env` 的 `CLOCK_MODE`（现行默认 system=真实时间）。

覆盖：
- 测试进程里默认仍是 replay（conftest 固定），既有确定性用例不受影响；
- 切到 system：healthz / demo/state 立刻报 system，now_utc ≈ 现实时间，tick 系列被拒（409）；
- 切回 replay：时钟归位到固定基准日，tick 恢复可用；
- 非法模式：422，且当前模式不被改动。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.core.clock import parse_dt
from app.core.config import current_clock_mode, get_settings, set_clock_mode

DEMO = "/api/v1/demo"
HEALTHZ = "/api/v1/healthz"


@pytest.fixture(autouse=True)
def _restore_clock_mode():
    """切换是进程级全局状态：不管用例怎么改，跑完都还原，避免污染其他文件。"""
    before = current_clock_mode()
    try:
        yield
    finally:
        set_clock_mode(before)


def _now_utc(client) -> datetime:
    return parse_dt(client.get(HEALTHZ).json()["now_utc"])


def _switch(client, headers, mode: str):
    return client.post(f"{DEMO}/actions/set-clock-mode", headers=headers, json={"clock_mode": mode})


def test_tests_run_on_replay_by_default(client, bootstrap, admin_headers):
    """测试进程由 conftest 固定 replay：可复现的既有用例（tick/到站送达）不受运行时开关影响。"""
    assert client.get(HEALTHZ).json()["clock_mode"] == "replay"
    assert client.get(f"{DEMO}/state", headers=admin_headers).json()["clock_mode"] == "replay"


def test_switch_to_system_uses_real_time_and_blocks_virtual_clock(client, bootstrap, admin_headers):
    real_before = datetime.now(UTC)
    response = _switch(client, admin_headers, "system")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["clock_mode"] == "system"
    assert body["previous_clock_mode"] == "replay"
    assert body["runtime_only"] is True
    assert body["offset_minutes"] == 0

    # 立即生效：不需要重启，healthz / demo/state / 根信息都报 system，且时间就是现实时间
    report = client.get(HEALTHZ).json()
    assert report["clock_mode"] == "system"
    assert abs((_now_utc(client) - real_before).total_seconds()) < 60
    assert client.get(f"{DEMO}/state", headers=admin_headers).json()["clock_mode"] == "system"
    assert client.get("/").json()["clock_mode"] == "system"

    # 虚拟时钟三件套在真实时间模式下明确拒绝（不是"点了没反应"）
    for path, payload in (
        (f"{DEMO}/actions/tick", {"minutes": 60}),
        (f"{DEMO}/actions/set-clock", {"target_utc": "2026-12-31T09:00:00+08:00"}),
    ):
        rejected = client.post(path, headers=admin_headers, json=payload)
        assert rejected.status_code == 409, rejected.text
        assert rejected.json()["error"]["code"] == "DEMO_CLOCK_DISABLED"
    assert client.post(f"{DEMO}/actions/advance-to-less", headers=admin_headers).status_code == 409


def test_switch_back_to_replay_restores_virtual_clock(client, bootstrap, admin_headers):
    assert _switch(client, admin_headers, "system").status_code == 200
    response = _switch(client, admin_headers, "replay")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["clock_mode"] == "replay"
    assert body["previous_clock_mode"] == "system"
    assert body["offset_minutes"] == 0
    assert body["warning"], "切回虚拟时钟要提示：业务时间回到基准日"

    base = parse_dt(get_settings().demo_base_date)
    assert abs((_now_utc(client) - base).total_seconds()) < 60, "切回 replay 后时钟应归位到固定基准日"

    tick = client.post(f"{DEMO}/actions/tick", headers=admin_headers, json={"minutes": 60})
    assert tick.status_code == 200, tick.text
    assert tick.json()["offset_minutes"] == 60
    assert abs((_now_utc(client) - (base + timedelta(minutes=60))).total_seconds()) < 60


def test_invalid_mode_is_rejected_and_keeps_current_mode(client, bootstrap, admin_headers):
    response = _switch(client, admin_headers, "banana")
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert current_clock_mode() == "replay"
