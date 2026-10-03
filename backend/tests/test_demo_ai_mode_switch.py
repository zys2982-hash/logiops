"""横幅「AI 模式切换」契约（Lead 维护）。

`POST /demo/actions/set-ai-mode` 让演示者不重启服务就能在 回放样本 / 真实大模型 之间切换。
要点：立即生效（进程内单例配置）、非法值 422、留审计、live 缺 key 时给出警告。
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.ai.runner import build_provider
from app.core.config import get_settings, set_ai_mode
from app.models import SystemSetting
from app.models.ops import AuditLog


@pytest.fixture(autouse=True)
def _restore_ai_mode():
    """测试结束后恢复 replay，避免污染同进程内的其它用例。"""
    yield
    set_ai_mode("replay")


def _set_mode(client, headers, mode: str) -> tuple[int, dict]:
    response = client.post("/api/v1/demo/actions/set-ai-mode", headers=headers, json={"ai_mode": mode})
    return response.status_code, response.json()


def test_switch_to_live_takes_effect_immediately(client, admin_headers):
    assert get_settings().ai_mode == "replay"
    assert build_provider().is_replay is True

    status, body = _set_mode(client, admin_headers, "live")
    assert status == 200, body
    assert body["ai_mode"] == "live"
    assert body["previous_ai_mode"] == "replay"
    # 立即生效：下一个分析就会走真实模型
    assert get_settings().ai_mode == "live"
    assert build_provider().is_replay is False

    state = client.get("/api/v1/demo/state", headers=admin_headers).json()
    assert state["ai_mode"] == "live"


def test_switch_back_to_replay(client, admin_headers):
    _set_mode(client, admin_headers, "live")
    status, body = _set_mode(client, admin_headers, "replay")
    assert status == 200, body
    assert body["ai_mode"] == "replay"
    assert build_provider().is_replay is True


def test_switch_accepts_case_and_whitespace(client, admin_headers):
    status, body = _set_mode(client, admin_headers, " LIVE ")
    assert status == 200, body
    assert body["ai_mode"] == "live"


def test_switch_rejects_unknown_mode(client, admin_headers):
    status, body = _set_mode(client, admin_headers, "turbo")
    assert status == 422, body
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert body["error"]["details"]["fields"][0]["loc"] == "ai_mode"
    # 非法输入不能改变现状
    assert get_settings().ai_mode == "replay"


def test_switch_writes_audit_and_does_not_touch_db(client, db_session, admin_headers):
    _set_mode(client, admin_headers, "live")
    audits = db_session.scalars(select(AuditLog).where(AuditLog.action == "demo.set_ai_mode")).all()
    assert audits, "切换 AI 模式必须留审计"
    assert audits[0].before_json == {"ai_mode": "replay"}
    assert audits[0].after_json == {"ai_mode": "live"}
    # 故意不写 system_setting：重启后回到 .env（避免"库里 live、实际 replay"）
    rows = db_session.scalars(
        select(SystemSetting).where(SystemSetting.setting_key == "ai.mode")
    ).all()
    assert rows == []


def test_live_without_api_key_returns_warning(client, admin_headers):
    status, body = _set_mode(client, admin_headers, "live")
    assert status == 200, body
    settings = get_settings()
    if settings.llm_api_key:
        assert body["warning"] is None
    else:
        assert "LLM_API_KEY" in str(body["warning"])
