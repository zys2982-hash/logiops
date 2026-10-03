"""AI 总开关默认停用（项目拥有者决定：先停掉全部 AI 逻辑，后续重建）。

运行时 `ai_enabled=False`（config 默认）→ ai_bridge 在唯一漏斗 `_call` 处拦住，
三个任务（T1 解析 / T2 分析 / T3 草稿）一律不执行、不调用大模型、不产出建议。
测试环境由 conftest 置 `AI_ENABLED=true`，所以 AI 层自身仍被覆盖（代码保留以便重建）。
"""

from __future__ import annotations

from app.core.config import get_settings
from app.services import ai_bridge


def test_bridge_is_disabled_by_default_and_returns_error():
    """停用时：不加载 app.ai.*、直接返回 ok=False + AI_DISABLED，绝不抛异常。"""
    settings = get_settings()
    original = settings.ai_enabled
    settings.ai_enabled = False
    try:
        result = ai_bridge._call("execute_analysis", None, None, 1)
        assert result["ok"] is False
        assert result["error_code"] == "AI_DISABLED"
        assert "停用" in result["error_message"]
    finally:
        settings.ai_enabled = original


def test_analyze_endpoint_degrades_cleanly_when_ai_disabled(client, db_session, bootstrap, admin_headers):
    """停用时发起分析：接口不 500，异常被退回 CONFIRMING，分析记录 FAILED 且 error_code=AI_DISABLED。"""
    from app.models import AiAnalysis
    from app.seed import reset_demo_data

    reset_demo_data(db_session, workspace_id=bootstrap["workspace_id"], with_knowledge=False)
    db_session.commit()

    page = client.get("/api/v1/exceptions?q=EX20260930001&page_size=1", headers=admin_headers).json()
    case = page["items"][0]

    settings = get_settings()
    original = settings.ai_enabled
    settings.ai_enabled = False
    try:
        response = client.post(
            f"/api/v1/exceptions/{case['id']}/analyze",
            headers=admin_headers,
            json={"expected_version": case["version"]},
        )
        assert response.status_code in (200, 202), response.text
        body = response.json()
        assert body.get("status") == "FAILED", f"AI 停用时分析应落 FAILED，实际 {body}"
        assert body.get("error_code") == "AI_DISABLED"
    finally:
        settings.ai_enabled = original

    refreshed = client.get(f"/api/v1/exceptions/{case['id']}", headers=admin_headers).json()
    assert refreshed["status"] == "CONFIRMING", "AI 失败后异常应退回 CONFIRMING，人工可继续处理"
    rows = (
        db_session.query(AiAnalysis)
        .filter(AiAnalysis.exception_id == case["id"], AiAnalysis.error_code == "AI_DISABLED")
        .all()
    )
    assert rows, "应留下一条 error_code=AI_DISABLED 的分析记录（可审计）"
