"""确定性模板回退（§11.8「现场无网络」+ 无 API Key 也能完整演示）。

只在"replay 模式找不到对应 input_hash 的 fixture"时使用：
输入固定取自 read_models 的事实基线 → 输出 100% 确定、可复现、且必然通过 guard 的事实校验。
model 记为 "template"，is_replay=True。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from app.ai import facts as F
from app.ai.tools import ToolOutcome
from app.core.clock import LOCAL_TZ, UTC, now_utc

REPAIRING_STATES = {"REPAIRING", "WAITING_PARTS", "BREAKDOWN"}
CITY_HINTS = (
    "北京", "上海", "天津", "重庆", "广州", "深圳", "杭州", "南京", "苏州", "无锡", "宁波", "合肥",
    "济南", "青岛", "烟台", "潍坊", "临沂", "德州", "郑州", "武汉", "长沙", "南昌", "福州", "厦门",
    "成都", "西安", "昆明", "贵阳", "南宁", "海口", "太原", "石家庄", "保定", "唐山", "沈阳", "大连",
    "长春", "哈尔滨", "徐州", "常州", "嘉兴", "南通", "温州", "泉州", "东莞", "佛山", "珠海", "兰州",
    "银川", "西宁", "乌鲁木齐", "呼和浩特", "洛阳", "开封", "淄博", "泰安", "聊城", "沧州", "廊坊",
)
PERIOD_DEFAULT_HOUR = {
    "凌晨": 5,
    "早上": 8,
    "上午": 9,
    "中午": 12,
    "下午": 14,
    "傍晚": 18,
    "晚上": 20,
}
EVENING_PERIODS = {"中午", "下午", "傍晚", "晚上"}

# 车辆状态关键词（与 backend-domain app/services/exceptions.py::_fallback_parse_result 同口径）
WAITING_PARTS_KEYWORDS = (
    "等待配件", "等配件", "等件", "缺件", "缺配件", "没有配件", "配件没到", "待件", "等料",
)
REPAIRING_KEYWORDS = ("修理厂", "维修", "修理", "修车", "正在修", "在修", "抢修", "维修中")
BREAKDOWN_KEYWORDS = ("抛锚", "爆胎", "故障", "熄火", "无法行驶", "走不了", "开不动", "趴窝", "坏了")
MOVING_KEYWORDS = (
    "已恢复", "已修好", "修好了", "继续行驶", "正常行驶", "已通行", "已出发", "恢复行驶",
)


# --- T1 模板 ---------------------------------------------------------------
def build_t1_output(
    *,
    raw_text: str,
    now: Any = None,
    occurred_at: Any = None,
    expected_eta_at: Any = None,
) -> dict[str, Any]:
    text = raw_text or ""
    now_dt = F.parse_any_dt(now) or now_utc()
    occurred_dt = F.parse_any_dt(occurred_at) or now_dt

    exception_type = _detect_exception_type(text)
    status = _detect_status(text)
    location = _detect_location(text)
    recovery = _detect_recovery_at(text, now_dt=now_dt, fallback_eta=expected_eta_at)
    if recovery is not None and recovery > occurred_dt + timedelta(hours=48):
        recovery = None
    if recovery is not None and recovery < occurred_dt:
        recovery = recovery + timedelta(days=1)

    confidence = 0.55
    if location:
        confidence += 0.15
    if recovery:
        confidence += 0.15
    if status != "UNKNOWN":
        confidence += 0.05
    confidence = round(min(0.9, max(0.3, confidence)), 2)

    missing: list[str] = []
    if not location:
        missing.append("具体位置")
    if recovery is None:
        missing.append("预计恢复时间")
    if not missing and exception_type == "OTHER":
        missing.append("异常类型无法归类")

    return {
        "exception_type": exception_type,
        "location": location or "未提供",
        "status": status,
        "estimated_recovery_at": recovery.isoformat().replace("+00:00", "Z") if recovery else None,
        "confidence": confidence,
        "missing_info": missing[:5],
    }


def _detect_exception_type(text: str) -> str:
    keywords = (
        "爆胎", "抛锚", "故障", "修理", "维修", "换胎", "坏", "配件", "发动机", "轮胎",
        "熄火", "无法行驶", "拖车",
    )
    if any(word in text for word in keywords):
        return "VEHICLE_BREAKDOWN"
    if any(word in text for word in ("堵", "事故", "封路", "排队", "通行", "限行", "交通")):
        return "DELAY_RISK"
    return "OTHER"


def _detect_status(text: str) -> str:
    """车辆状态关键词口径与 backend-domain `_fallback_parse_result` 保持一致。

    优先级（更具体者优先）：
      1) 等配件/缺件 → WAITING_PARTS
      2) 维修/修理/修车/抢修 → REPAIRING
      3) 抛锚/爆胎/故障/熄火（无维修信息）→ BREAKDOWN
      4) 已恢复/继续行驶/已出发 → MOVING
      5) 否则 UNKNOWN
    """
    if any(word in text for word in WAITING_PARTS_KEYWORDS):
        return "WAITING_PARTS"
    if any(word in text for word in REPAIRING_KEYWORDS):
        return "REPAIRING"
    if any(word in text for word in BREAKDOWN_KEYWORDS):
        return "BREAKDOWN"
    if any(word in text for word in MOVING_KEYWORDS):
        return "MOVING"
    return "UNKNOWN"


def _detect_location(text: str) -> str | None:
    for city in CITY_HINTS:
        if city in text:
            return city
    match = re.search(r"在([\u4e00-\u9fa5]{2,8}?)(?=附近|服务区|，|,|。|；|;|、|\s|$)", text)
    if match:
        candidate = match.group(1)
        if candidate in {"这里", "当地", "现场", "路上", "中途"}:
            return candidate
        return candidate
    return None


def _detect_recovery_at(text: str, *, now_dt: datetime, fallback_eta: Any = None) -> datetime | None:
    base = now_dt.astimezone(LOCAL_TZ)
    day_offset = 0
    if "后天" in text:
        day_offset = 2
    elif "明天" in text or "次日" in text:
        day_offset = 1
    elif "今天" in text or "当晚" in text or "今晚" in text:
        day_offset = 0

    period: str | None = None
    for keyword in PERIOD_DEFAULT_HOUR:
        if keyword in text:
            period = keyword
            break

    hour: int | None = None
    minute = 0
    match = re.search(r"(\d{1,2})\s*[点:：]\s*(\d{1,2})?", text)
    if match and "小时" not in text[max(0, match.start() - 1): match.start() + 1]:
        hour = int(match.group(1))
        if match.group(2):
            minute = int(match.group(2))
        elif "半" in text[match.end(): match.end() + 2]:
            minute = 30
        if period in EVENING_PERIODS and hour < 12:
            hour += 12
        hour = min(hour, 23)
    elif period is not None and re.search(rf"{period}\s*(左右|前后)?", text):
        hour = PERIOD_DEFAULT_HOUR[period]

    if hour is None:
        relative = re.search(r"(\d{1,2})\s*(?:个)?小时后", text)
        if relative:
            return now_dt + timedelta(hours=int(relative.group(1)))
        if fallback_eta:
            return F.parse_any_dt(fallback_eta)
        return None

    target = (base + timedelta(days=day_offset)).replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target < base:
        target = target + timedelta(days=1)
    return target.astimezone(UTC)


# --- T2 模板 ---------------------------------------------------------------
def build_t2_output(
    *,
    facts: dict[str, Any],
    tool_results: dict[str, ToolOutcome] | None = None,
    knowledge: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    results = tool_results or {}
    chunks = knowledge if knowledge is not None else _knowledge_from(results)
    case = F.case_of(facts)
    order = F.order_of(facts)
    customer = F.customer_of(facts)
    vehicle = F.vehicle_of(facts)
    sla = F.sla_of(facts)
    message = F.latest_message_of(facts)
    delay = F.backend_delay_minutes(facts) or 0
    breached = F.backend_sla_breached(facts)
    level = str(customer.get("level") or "NORMAL")
    eta_text = F.local_str(case.get("expected_eta_at") or sla.get("expected_eta_at"))
    promised_text = F.local_str(case.get("promised_delivery_at") or sla.get("promised_delivery_at"))

    summary_parts = [
        f"{order.get('order_no') or '-'} {order.get('origin_city') or '-'}→{order.get('dest_city') or '-'}：",
        f"{_type_label(case.get('type'))}；",
        f"客户 {customer.get('name') or '-'}（{level}）承诺 {promised_text}，",
        f"预计 {eta_text} 到达，延误 {delay} 分钟",
        "，已超出承诺时间" if breached else "",
        f"；车辆 {vehicle.get('plate_no') or '-'}({vehicle.get('status') or '-'})",
    ]
    summary = _trim("".join(summary_parts), 300)

    note_pool = [
        str(case.get("root_cause_note") or ""),
        str(case.get("impact_summary") or ""),
        str(message.get("raw_text") or ""),
    ]
    note = next((item for item in note_pool if item), "") or f"{_type_label(case.get('type'))}，承运商未给出详细原因"
    note = _trim(note, 200)

    suggestions: list[dict[str, Any]] = []
    if case.get("expected_eta_at"):
        suggestions.append(
            {
                "code": "UPDATE_ETA",
                "title": f"将 ETA 更新为 {eta_text}",
                "rationale": "依据承运商反馈与系统规则重算的预计到达时间",
            }
        )
    if breached or delay > 0 or str(vehicle.get("status")) in REPAIRING_STATES:
        followup_at = F.local_str(now_utc() + timedelta(minutes=30), "%H:%M")
        suggestions.append(
            {
                "code": "CREATE_FOLLOWUP",
                "title": f"{followup_at} 回访承运商确认恢复情况",
                "rationale": "车辆处于故障/延误状态，需在承诺时间前确认进展",
                "assignee_role": "OPERATOR",
            }
        )
    if breached or level in {"VIP", "SVIP"}:
        suggestions.append(
            {
                "code": "SAVE_NOTICE",
                "title": "生成并发送延误通知给客户",
                "rationale": "客户等级与违约状态触达通知规范要求",
            }
        )
    if not suggestions:
        suggestions.append(
            {
                "code": "CREATE_FOLLOWUP",
                "title": "持续跟进订单状态直至送达",
                "rationale": "当前无违约且无车辆故障，保持常规跟进",
            }
        )

    open_questions: list[str] = []
    if not chunks:
        open_questions.append("未找到相关规范，建议人工确认处置口径")
    if str(vehicle.get("status")) in REPAIRING_STATES:
        open_questions.append("修理厂是否已确认配件到位？")
    if not case.get("expected_eta_at"):
        open_questions.append("承运商未给出预计恢复时间")
    if not message:
        open_questions.append("承运商是否已确认最新位置？")

    return {
        "summary": summary,
        "root_cause": {"code": _detect_root_cause(case, note), "note": note},
        "impact": {"delay_minutes": int(delay), "sla_breached": bool(breached), "affected_customer_level": level},
        "suggestions": suggestions[:5],
        "open_questions": open_questions[:5],
        "evidence_refs": _evidence_refs(results, chunks),
    }


def _type_label(value: Any) -> str:
    return {"VEHICLE_BREAKDOWN": "车辆故障", "DELAY_RISK": "延误风险"}.get(str(value), "运输异常")


def _detect_root_cause(case: dict[str, Any], note: str) -> str:
    if str(case.get("type")) == "VEHICLE_BREAKDOWN":
        return "VEHICLE_BREAKDOWN"
    text = f"{note}{case.get('impact_summary') or ''}"
    if any(word in text for word in ("堵", "事故", "交通", "封路", "限行")):
        return "TRAFFIC"
    if any(word in text for word in ("雨", "雪", "台风", "大雾", "暴雨", "天气")):
        return "WEATHER"
    if any(word in text for word in ("海关", "清关", "报关", "查验")):
        return "CUSTOMS"
    if any(word in text for word in ("客户", "收货人", "拒收", "没人卸货")):
        return "CUSTOMER"
    return "VEHICLE_BREAKDOWN" if str(case.get("type")) == "VEHICLE_BREAKDOWN" else "UNKNOWN"


def _knowledge_from(results: dict[str, ToolOutcome]) -> list[dict[str, Any]]:
    outcome = results.get("search_knowledge")
    if outcome is None or not isinstance(outcome.payload, list):
        return []
    return [chunk for chunk in outcome.payload if isinstance(chunk, dict)]


def _evidence_refs(results: dict[str, ToolOutcome], chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []

    def _payload(name: str) -> Any:
        outcome = results.get(name)
        return outcome.payload if outcome is not None and outcome.status == "OK" else None

    order = _payload("get_order")
    if isinstance(order, dict) and order.get("id") is not None:
        refs.append({"type": "ORDER", "id": order["id"], "note": f"订单 {order.get('order_no')}"})

    events = _payload("get_tracking_events")
    if isinstance(events, list) and events:
        latest = events[0]
        refs.append(
            {
                "type": "TRACKING_EVENT",
                "id": latest.get("id"),
                "note": f"{F.local_short(latest.get('occurred_at'))} 位于 {latest.get('city')}",
            }
        )

    vehicle = _payload("get_vehicle")
    if isinstance(vehicle, dict) and vehicle.get("id") is not None:
        refs.append(
            {"type": "VEHICLE", "id": vehicle["id"], "note": f"{vehicle.get('plate_no')} {vehicle.get('status')}"}
        )

    if chunks:
        first = chunks[0]
        refs.append(
            {
                "type": "KNOWLEDGE_CHUNK",
                "id": first.get("chunk_id"),
                "note": _trim(first.get("content"), 80),
                "doc": first.get("doc_title"),
                "section": first.get("section_path"),
            }
        )
    return [ref for ref in refs if ref.get("id") is not None]


# --- T3 模板 ---------------------------------------------------------------
def build_t3_output(
    *,
    facts: dict[str, Any],
    analysis: dict[str, Any] | None = None,
) -> dict[str, Any]:
    analysis = analysis or {}
    case = F.case_of(facts)
    order = F.order_of(facts)
    customer = F.customer_of(facts)
    sla = F.sla_of(facts)
    delay = F.backend_delay_minutes(facts) or 0
    breached = F.backend_sla_breached(facts)
    order_no = str(order.get("order_no") or case.get("case_no") or "-")
    eta_text = F.local_str(case.get("expected_eta_at") or sla.get("expected_eta_at"))
    promised_text = F.local_str(case.get("promised_delivery_at") or sla.get("promised_delivery_at"))
    customer_name = str(customer.get("name") or "尊敬的客户")
    root_cause = str((analysis.get("root_cause") or {}).get("note") or case.get("root_cause_note") or "运输异常")

    if breached:
        tone = "APOLOGETIC"
        subject = f"【延误通知】订单 {order_no} 预计 {eta_text} 到达"
        content = (
            f"尊敬的 {customer_name} 客户：非常抱歉，您的订单 {order_no} 在运输途中遇到{_trim(root_cause, 40)}，"
            f"预计 {eta_text} 到达（原承诺 {promised_text}），较承诺时间延误 {int(delay)} 分钟。"
            f"我们已安排专人跟进并优先处理，给您带来不便深表歉意，感谢您的理解。"
        )
    else:
        tone = "FORMAL"
        subject = f"【运输进展】订单 {order_no} 预计 {eta_text} 到达"
        content = (
            f"尊敬的 {customer_name} 客户：您的订单 {order_no} 运输正常，预计 {eta_text} 到达"
            f"（承诺 {promised_text}）。当前延误 {int(delay)} 分钟，我们正在持续跟进，"
            f"如有变化将第一时间通知您。"
        )
    return {"subject": _trim(subject, 60), "content": _trim(content, 500), "tone": tone}


def _trim(value: Any, limit: int) -> str:
    text = "" if value is None else str(value)
    return text[:limit]


__all__ = ["build_t1_output", "build_t2_output", "build_t3_output"]
