"""Prompt 上下文构造函数（唯一入口）。

单测断言：`set(build_*_context(...)) == load_prompt(task).variables`。
所有函数"无论入参如何"都返回固定 key 集合，缺失值用空串/占位符兜底。
"""

from __future__ import annotations

from typing import Any

EMPTY = "-"


def _kv(value: Any, default: str = EMPTY) -> str:
    if value is None or value == "":
        return default
    return str(value)


def _risk_factors_text(facts: dict[str, Any]) -> str:
    """风险分构成（系统已算好，模型只引用不算）。

    形如：`延误时长(+2：实际延误 270 分钟)、VIP 客户(+1：VIP 客户需优先处理) → 合计 3 分，等级 HIGH`。
    """
    risk = facts.get("risk") or {}
    case = facts.get("exception") or {}
    factors = [item for item in (risk.get("factors") or []) if isinstance(item, dict)]
    score = risk.get("score")
    if score is None:
        score = case.get("risk_score")
    level = risk.get("level") or case.get("risk_level") or case.get("level")
    if not factors:
        return f"无加分项（风险分 {score if score is not None else 0}，等级 {level or EMPTY}）"
    parts = [f"{item.get('label')}(+{item.get('weight')}：{item.get('detail')})" for item in factors]
    return f"{'、'.join(parts)} → 合计 {score if score is not None else 0} 分，等级 {level or EMPTY}"


def _initial_risk_text(facts: dict[str, Any]) -> str:
    """建单时**冻结**的风险快照（只作参考，口径 Q5a）：`4 分 / CRITICAL（延误时长(+3)、VIP 客户(+1)）`。"""
    initial = ((facts.get("exception") or {}).get("initial_risk")) or {}
    score = initial.get("score")
    if score is None:
        return "历史数据无快照"
    factors = [item for item in (initial.get("factors") or []) if isinstance(item, dict)]
    parts = "、".join(f"{item.get('label')}(+{item.get('weight')})" for item in factors) or "无加分项"
    return f"{score} 分 / {initial.get('level') or EMPTY}（{parts}）"


def build_t1_context(
    *,
    raw_text: str,
    now: str | None = None,
    order_no: Any = None,
    origin_city: Any = None,
    dest_city: Any = None,
    plate_no: Any = None,
    vehicle_status: Any = None,
    expected_eta_at: Any = None,
    occurred_at: Any = None,
) -> dict[str, str]:
    return {
        "raw_text": _kv(raw_text),
        "now": _kv(now),
        "order_no": _kv(order_no),
        "origin_city": _kv(origin_city),
        "dest_city": _kv(dest_city),
        "plate_no": _kv(plate_no),
        "vehicle_status": _kv(vehicle_status),
        "expected_eta_at": _kv(expected_eta_at),
        "occurred_at": _kv(occurred_at),
    }


def build_t2_context(
    *,
    facts: dict[str, Any] | None = None,
    knowledge: list[dict[str, Any]] | None = None,
    tool_observations: str = "",
) -> dict[str, str]:
    facts = facts or {}
    case = facts.get("exception") or {}
    order = facts.get("order") or {}
    customer = facts.get("customer") or {}
    sla = facts.get("sla") or {}
    vehicle = facts.get("vehicle") or {}
    risk = facts.get("risk") or {}
    events = facts.get("tracking_events") or []
    last_event = events[0] if events else {}
    message = facts.get("latest_carrier_message") or {}
    chunks = knowledge or []
    knowledge_text = "\n".join(
        f"[{index + 1}] {chunk.get('doc_title')}#{chunk.get('section_path')} "
        f"(chunk_id={chunk.get('chunk_id')}) {_trim(chunk.get('content'), 160)}"
        for index, chunk in enumerate(chunks)
    )
    return {
        "case_no": _kv(case.get("case_no")),
        "exception_type": _kv(case.get("type_label") or case.get("type")),
        "current_level": _kv(case.get("level_label") or case.get("level")),
        "order_no": _kv(order.get("order_no")),
        # 实体主键（真实模型必须靠这些 id 调工具；只给业务编号会让它拿订单号当主键猜）
        "exception_id": _kv(case.get("id")),
        "order_id": _kv(order.get("id")),
        "customer_id": _kv(customer.get("id")),
        "vehicle_id": _kv(vehicle.get("id")),
        "id_hint": (
            "以上 id 是数据库主键（整数）。调用 get_order / get_customer / get_customer_sla / "
            "get_vehicle / get_exception_history 时**只能**传这些 id；"
            "订单号、车牌、异常单号等业务编号仅用于文案表述。"
        ),
        "origin_city": _kv(order.get("origin_city")),
        "dest_city": _kv(order.get("dest_city")),
        "customer_name": _kv(customer.get("name")),
        "customer_level": _kv(customer.get("level")),
        "promised_delivery_at": _kv(sla.get("promised_delivery_at") or case.get("promised_delivery_at")),
        "expected_eta_at": _kv(case.get("expected_eta_at") or sla.get("expected_eta_at")),
        "sla_delay_minutes": _kv(_backend_delay(facts), "0"),
        "sla_breached": _kv(case.get("sla_breached"), "false"),
        "risk_level": _kv(risk.get("level_label") or case.get("level_label") or risk.get("level") or case.get("level")),
        # 风险分构成：AI 的处置建议要**从这些因子出发**逐项给方案（用户口径 2026-10-08）
        "risk_factors": _kv(_risk_factors_text(facts)),
        # 建单时冻结的快照：仅作参考 —— 建议一律按"当前"因子给（口径 Q5a）
        "initial_risk": _kv(_initial_risk_text(facts)),
        "vehicle_plate": _kv(vehicle.get("plate_no")),
        "vehicle_status": _kv(vehicle.get("status_label") or vehicle.get("status")),
        "last_move_city": _kv(last_event.get("city")),
        "last_move_at": _kv(last_event.get("occurred_at")),
        "latest_message": _kv(message.get("raw_text")),
        "tracking_count": _kv(len(events), "0"),
        "tool_observations": _kv(tool_observations),
        "knowledge_chunks": _kv(knowledge_text),
        "now": _kv(facts.get("now")),
    }


def build_t3_context(*, facts: dict[str, Any] | None = None, analysis: dict[str, Any] | None = None) -> dict[str, str]:
    facts = facts or {}
    analysis = analysis or {}
    case = facts.get("exception") or {}
    order = facts.get("order") or {}
    customer = facts.get("customer") or {}
    sla = facts.get("sla") or {}
    root_cause = analysis.get("root_cause") or {}
    suggestions = analysis.get("suggestions") or []
    return {
        "order_no": _kv(order.get("order_no")),
        "customer_name": _kv(customer.get("name")),
        "customer_level": _kv(customer.get("level")),
        "promised_delivery_at": _kv(sla.get("promised_delivery_at") or case.get("promised_delivery_at")),
        "expected_eta_at": _kv(case.get("expected_eta_at") or sla.get("expected_eta_at")),
        "delay_minutes": _kv(_backend_delay(facts), "0"),
        "sla_breached": _kv(case.get("sla_breached"), "false"),
        "current_status": _kv(case.get("status")),
        "impact_summary": _kv(case.get("impact_summary")),
        "root_cause_note": _kv(root_cause.get("note") or case.get("root_cause_note")),
        "progress": _progress(suggestions),
        "analysis_summary": _kv(analysis.get("summary")),
        "now": _kv(facts.get("now")),
    }


def _backend_delay(facts: dict[str, Any]) -> int | None:
    case = facts.get("exception") or {}
    delay = case.get("sla_delay_minutes")
    if delay is None:
        delay = (facts.get("sla") or {}).get("delay_minutes")
    return delay


def _progress(suggestions: list[dict[str, Any]]) -> str:
    titles = [str(item.get("title")) for item in suggestions if item.get("title")]
    return "；".join(titles) if titles else EMPTY


def _trim(value: Any, limit: int) -> str:
    text = "" if value is None else str(value).replace("\n", " ")
    return text[:limit]


__all__ = ["build_t1_context", "build_t2_context", "build_t3_context"]
