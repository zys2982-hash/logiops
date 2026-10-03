"""AI 层桥接（唯一调用点集中在这里，便于审计与降级）。

契约（由 ai-engine 智能体实现，本模块只按名字调用并容错）：
    app.ai.runner.execute_analysis(session, repos, analysis_id) -> dict
    app.ai.runner.parse_message(session, repos, message_id) -> dict
    app.ai.runner.draft_notice(session, repos, exception_id, analysis_id=None) -> dict

容错策略（基线文档 §11.8）：
- ImportError / 任意异常 → 返回 ok=False + error_code（LLM_UNAVAILABLE / AI_OUTPUT_INVALID），
  调用方把 ai_analysis 置 FAILED 并把异常退回 CONFIRMING，业务接口不 500。
- SAVE_NOTICE 执行时 AI 不可用 → 用事实快照拼确定性模板（fallback_notice）。
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.config import get_settings
from app.services import read_models

logger = logging.getLogger("logiops.ai_bridge")

LLM_UNAVAILABLE = "LLM_UNAVAILABLE"
AI_OUTPUT_INVALID = "AI_OUTPUT_INVALID"
AI_DISABLED = "AI_DISABLED"

_INVALID_HINTS = ("schema", "invalid", "validation", "json", "事实", "不一致", "编造")


def _load_runner() -> Any | None:
    try:
        from app.ai import runner  # noqa: PLC0415
    except Exception as exc:  # ImportError 及其它初始化异常都算"AI 不可用"
        logger.warning("AI 层不可用（%s: %s），走降级路径", type(exc).__name__, exc)
        return None
    return runner


def classify_error(exc: BaseException) -> str:
    name = type(exc).__name__.lower()
    text = f"{name} {exc}".lower()
    if "validation" in name or any(hint in text for hint in _INVALID_HINTS):
        return AI_OUTPUT_INVALID
    return LLM_UNAVAILABLE


def _call(name: str, *args: Any, retry_without_last: bool = False, **kwargs: Any) -> dict[str, Any]:
    # AI 总开关（默认关闭）：一处拦住 T1/T2/T3 全部三个任务。
    # 关闭期间不加载 app.ai.*、不调用大模型、不产出任何建议；代码保留以便后续重建。
    if not get_settings().ai_enabled:
        return {
            "ok": False,
            "error_code": AI_DISABLED,
            "error_message": "AI 已停用（待重构）：本次不做大模型调用，也不产出建议",
        }
    runner = _load_runner()
    if runner is None or not hasattr(runner, name):
        return {"ok": False, "error_code": LLM_UNAVAILABLE, "error_message": f"AI 层未实现 {name}"}
    func = getattr(runner, name)
    try:
        result = func(*args, **kwargs)
    except TypeError:
        if not retry_without_last or not args:
            return {"ok": False, "error_code": LLM_UNAVAILABLE, "error_message": f"{name} 调用签名不匹配"}
        try:
            result = func(*args[:-1], **kwargs)
        except Exception as exc:  # noqa: BLE001 - 桥接层必须吞掉所有异常
            logger.warning("AI 调用失败 %s：%s", name, exc)
            return {"ok": False, "error_code": classify_error(exc), "error_message": str(exc)[:255]}
    except Exception as exc:  # noqa: BLE001 - 桥接层必须吞掉所有异常
        logger.warning("AI 调用失败 %s：%s", name, exc)
        return {"ok": False, "error_code": classify_error(exc), "error_message": str(exc)[:255]}
    return {"ok": True, "result": result if isinstance(result, dict) else {"output": result}}


def execute_analysis(session: Any, repos: Any, analysis_id: int) -> dict[str, Any]:
    return _call("execute_analysis", session, repos, analysis_id)


def parse_message(session: Any, repos: Any, message_id: int) -> dict[str, Any]:
    return _call("parse_message", session, repos, message_id)


def draft_notice(
    session: Any, repos: Any, exception_id: int, analysis_id: int | None = None
) -> dict[str, Any]:
    return _call("draft_notice", session, repos, exception_id, analysis_id, retry_without_last=True)


def fallback_notice(facts: dict[str, Any]) -> dict[str, str]:
    """AI 不可用时的确定性通知模板：只用事实快照里的数字/单号（§11.5）。"""
    order = facts.get("order") or {}
    customer = facts.get("customer") or {}
    exception = facts.get("exception") or {}
    sla = facts.get("sla") or {}

    order_no = order.get("order_no") or "未知单号"
    customer_name = customer.get("name") or "客户"
    expected = order.get("current_eta_at") or "待确认"
    promised = order.get("promised_delivery_at") or "待确认"
    delay = sla.get("delay_minutes")
    if delay is None:
        delay = exception.get("sla_delay_minutes") or 0
    origin = order.get("origin_city") or "-"
    dest = order.get("dest_city") or "-"
    cause = exception.get("root_cause_note") or exception.get("impact_summary") or "运输异常处理中"

    subject = f"【运输延误告知】{order_no} {origin}→{dest}"[:60]
    content = (
        f"尊敬的 {customer_name}：您好。\n"
        f"您的货物（运单号 {order_no}，{origin}→{dest}）在运输途中遇到异常，我们正在全力处理。\n"
        f"当前预计到达时间：{expected}；原承诺到达时间：{promised}；预计延误约 {int(delay)} 分钟。\n"
        f"异常原因简述：{cause}。\n"
        f"我们会持续跟进并在有进展时第一时间同步，感谢理解。"
    )
    return {"subject": subject, "content": content[:500], "tone": "APOLOGETIC"}


def notice_from_ai_or_template(
    session: Any,
    repos: Any,
    exception_id: int,
    analysis_id: int | None = None,
) -> tuple[dict[str, str], str]:
    """返回 (通知草稿, 来源标记)；来源标记为 AI 或 TEMPLATE。"""
    facts = read_models.exception_facts(repos, exception_id)
    call = draft_notice(session, repos, exception_id, analysis_id)
    if call.get("ok"):
        result = call.get("result") or {}
        payload = result.get("notice") if isinstance(result.get("notice"), dict) else result
        content = str(payload.get("content") or "").strip()
        if content:
            return (
                {
                    "subject": str(payload.get("subject") or fallback_notice(facts)["subject"])[:128],
                    "content": content[:500],
                    "tone": str(payload.get("tone") or "FORMAL"),
                },
                "AI",
            )
    return fallback_notice(facts), "TEMPLATE"


__all__ = [
    "AI_OUTPUT_INVALID",
    "LLM_UNAVAILABLE",
    "classify_error",
    "draft_notice",
    "execute_analysis",
    "fallback_notice",
    "notice_from_ai_or_template",
    "parse_message",
]
