"""演示工具「时间跳转」契约（Lead 维护）。

`POST /demo/actions/set-clock` = 把虚拟时钟**直接跳到**选定时刻（年月日时分秒）。
与 tick 的区别：tick 是"往前走 N 分钟"，跳转是"直接到那一刻"，且**不触发**业务链路。
"""

from __future__ import annotations

from sqlalchemy import select

from app.models import SystemSetting
from app.models.ops import AuditLog


def _set_clock(client, headers, target: str) -> tuple[int, dict]:
    response = client.post("/api/v1/demo/actions/set-clock", headers=headers, json={"target_utc": target})
    return response.status_code, response.json()


def test_set_clock_jumps_to_selected_time(client, operator_headers):
    status, body = _set_clock(client, operator_headers, "2026-10-05T06:30:00Z")
    assert status == 200, body
    assert body["now_utc"] == "2026-10-05T06:30:00+00:00"
    assert body["base_date"] == "2026-10-05T06:30:00+00:00"
    assert body["offset_minutes"] == 0

    state = client.get("/api/v1/demo/state", headers=operator_headers).json()
    assert state["now_utc"] == "2026-10-05T06:30:00+00:00"
    assert state["base_date"] == "2026-10-05T06:30:00+00:00", "基准日应跟随跳转锚点，保证 基准日+偏移=业务时间"
    assert state["offset_minutes"] == 0


def test_clock_jump_keeps_second_precision(client, operator_headers):
    """秒级精确：目标不是整分钟也要完全命中（这是"选时分秒"的关键）。"""
    status, body = _set_clock(client, operator_headers, "2026-10-05T06:30:37Z")
    assert status == 200, body
    assert body["now_utc"] == "2026-10-05T06:30:37+00:00"


def test_clock_jump_accepts_local_offset(client, operator_headers):
    """带时区偏移的输入等价于对应 UTC 时刻（08:00+08:00 = 00:00Z）。"""
    status, body = _set_clock(client, operator_headers, "2026-10-05T08:00:00+08:00")
    assert status == 200, body
    assert body["now_utc"] == "2026-10-05T00:00:00+00:00"


def test_tick_after_jump_advances_from_new_anchor(client, operator_headers):
    """跳转后再快进，应在**新锚点**上继续走（而不是回到配置基准日）。"""
    _set_clock(client, operator_headers, "2026-10-05T06:30:00Z")
    tick = client.post("/api/v1/demo/actions/tick", headers=operator_headers, json={"minutes": 60})
    assert tick.status_code == 200, tick.text
    assert tick.json()["now_utc"] == "2026-10-05T07:30:00+00:00"

    state = client.get("/api/v1/demo/state", headers=operator_headers).json()
    assert state["base_date"] == "2026-10-05T06:30:00+00:00"
    assert state["offset_minutes"] == 60


def test_set_clock_rejects_invalid_and_out_of_range(client, operator_headers):
    status, body = _set_clock(client, operator_headers, "不是时间")
    assert status == 422, body
    error = body["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert error["details"]["fields"][0]["loc"] == "target_utc"

    status, body = _set_clock(client, operator_headers, "1900-01-01T00:00:00Z")
    assert status == 422, body
    assert "2020" in body["error"]["details"]["fields"][0]["msg"]


def test_set_clock_writes_audit_and_system_setting(client, db_session, operator_headers):
    _set_clock(client, operator_headers, "2026-10-05T06:30:00Z")
    rows = db_session.scalars(select(AuditLog).where(AuditLog.action == "demo.set_clock")).all()
    assert rows, "时间跳转必须留审计"
    assert rows[0].after_json["now_utc"] == "2026-10-05T06:30:00+00:00"

    settings = {
        row.setting_key: row.setting_value for row in db_session.scalars(select(SystemSetting))
    }
    assert settings["demo.base_date"].startswith("2026-10-05T06:30:00")
    assert settings["demo.clock_offset_minutes"] == "0"
