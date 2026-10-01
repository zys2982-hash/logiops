"""AI 受限层（T3 / 基线文档 §11）。

对外契约（backend-domain 只依赖这三个名字与 `app.ai.errors` 两个异常）：
    app.ai.runner.execute_analysis(session, repos, analysis_id)
    app.ai.runner.parse_message(session, repos, message_id)
    app.ai.runner.draft_notice(session, repos, exception_id, analysis_id=None)

分层：Runner → Agent（有界循环）→ Guard（schema+事实）→ Tool（7 个只读）→ read_models → DB。
默认 AI_MODE=replay：无 API Key、无网络也能完整演示（fixture 命中则回放，否则确定性模板回退）。
"""

from __future__ import annotations

__all__ = ["errors", "guard", "knowledge_index", "prompts", "replay", "runner", "schemas", "tools"]
