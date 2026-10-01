"""事实口径与时间解析工具（模板回退与 guard 共用）。

所有"后端口径"的取值只在这里定义一次：
- sla_delay_minutes：优先 exception_case.sla_delay_minutes，其次规则算出的 sla.delay_minutes
- sla_breached：exception_case.sla_breached（由规则写入）
时间统一走 Asia/Shanghai 展示、UTC 存储（§8.7）。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from app.core.clock import LOCAL_TZ, UTC

_MD_TIME_RE = re.compile(r"(\d{1,2})\s*月\s*(\d{1,2})\s*日\s*(\d{1,2})\s*[:：]\s*(\d{2})")
_FULL_DT_RE = re.compile(
    r"(\d{4})\s*[-/年]\s*(\d{1,2})\s*[-/月]\s*(\d{1,2})\s*日?[ T]?\s*(\d{1,2})\s*[:：]\s*(\d{2})"
)
_DATE_RE = re.compile(r"(\d{4})\s*[-/年]\s*(\d{1,2})\s*[-/月]\s*(\d{1,2})\s*日?")
_TIME_RE = re.compile(r"(?<!\d)(\d{1,2})\s*[:：]\s*(\d{2})(?!\d)")
_ORDER_NO_RE = re.compile(r"[A-Z]{2}\d{6,}")

DELAY_PATTERNS = (
    re.compile(r"延误\s*(\d+(?:\.\d+)?)\s*(分钟|min)", re.IGNORECASE),
    re.compile(r"延误\s*(\d+(?:\.\d+)?)\s*(小时|h|hour)", re.IGNORECASE),
    re.compile(r"(?:晚点|延迟)\s*(\d+(?:\.\d+)?)\s*(分钟|小时|min|h)", re.IGNORECASE),
)


# --- 事实取数 ---------------------------------------------------------------
def case_of(facts: dict[str, Any] | None) -> dict[str, Any]:
    return (facts or {}).get("exception") or {}


def order_of(facts: dict[str, Any] | None) -> dict[str, Any]:
    return (facts or {}).get("order") or {}


def customer_of(facts: dict[str, Any] | None) -> dict[str, Any]:
    return (facts or {}).get("customer") or {}


def sla_of(facts: dict[str, Any] | None) -> dict[str, Any]:
    return (facts or {}).get("sla") or {}


def vehicle_of(facts: dict[str, Any] | None) -> dict[str, Any]:
    return (facts or {}).get("vehicle") or {}


def risk_of(facts: dict[str, Any] | None) -> dict[str, Any]:
    return (facts or {}).get("risk") or {}


def latest_message_of(facts: dict[str, Any] | None) -> dict[str, Any]:
    return (facts or {}).get("latest_carrier_message") or {}


def backend_delay_minutes(facts: dict[str, Any] | None) -> int | None:
    """权威延误分钟（后端口径）。"""
    delay = case_of(facts).get("sla_delay_minutes")
    if delay is None:
        delay = sla_of(facts).get("delay_minutes")
    return None if delay is None else int(delay)


def backend_sla_breached(facts: dict[str, Any] | None) -> bool:
    return bool(case_of(facts).get("sla_breached"))


def backend_risk_level(facts: dict[str, Any] | None) -> str:
    """风险等级只由 §8.6 规则决定，LLM 无权改。"""
    case = case_of(facts)
    return str(risk_of(facts).get("level") or case.get("risk_level") or case.get("level") or "LOW")


# --- 时间 -------------------------------------------------------------------
def parse_any_dt(value: Any) -> datetime | None:
    """解析为 aware UTC。朴素值按 DB 约定当 UTC（不是 +08:00）。"""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    text = str(value).strip()
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def local_str(value: Any, fmt: str = "%Y-%m-%d %H:%M") -> str:
    dt = parse_any_dt(value)
    if dt is None:
        return "-"
    return dt.astimezone(LOCAL_TZ).strftime(fmt)


def local_short(value: Any) -> str:
    return local_str(value, "%m-%d %H:%M")


# --- 文本中的时间/单号识别（guard 用） ---------------------------------------
def canonical_full(date: str, hour: int, minute: int) -> str:
    return f"{date} {hour:02d}:{minute:02d}"


def canonical_md(month: int, day: int, hour: int, minute: int) -> str:
    return f"{month:02d}-{day:02d} {hour:02d}:{minute:02d}"


def canonical_date(date: str) -> str:
    return date


def canonical_time(hour: int, minute: int) -> str:
    return f"{hour:02d}:{minute:02d}"


def allowed_datetime_keys(values: list[Any]) -> dict[str, set[str]]:
    """事实时间的归一化 key 集合，按形态分组：full / md / date / time（本地与 UTC 都算合法）。"""
    keys: dict[str, set[str]] = {"full": set(), "md": set(), "date": set(), "time": set()}
    for value in values:
        dt = parse_any_dt(value)
        if dt is None:
            continue
        for moment in (dt.astimezone(LOCAL_TZ), dt.astimezone(UTC)):
            date = moment.strftime("%Y-%m-%d")
            keys["full"].add(canonical_full(date, moment.hour, moment.minute))
            keys["md"].add(canonical_md(moment.month, moment.day, moment.hour, moment.minute))
            keys["date"].add(canonical_date(date))
            keys["time"].add(canonical_time(moment.hour, moment.minute))
    return keys


def extract_datetime_spans(text: str) -> list[tuple[str, str, str]]:
    """返回 (形态, 原文, 归一化 key) —— 形态 ∈ full/md/date/time。"""
    spans: list[tuple[str, str, str]] = []
    consumed: list[tuple[int, int]] = []

    def _overlaps(start: int, end: int) -> bool:
        return any(start < other_end and end > other_start for other_start, other_end in consumed)

    for match in _FULL_DT_RE.finditer(text):
        if _overlaps(match.start(), match.end()):
            continue
        year, month, day, hour, minute = (int(part) for part in match.groups())
        date = f"{year:04d}-{month:02d}-{day:02d}"
        spans.append(("full", match.group(0), canonical_full(date, hour, minute)))
        consumed.append((match.start(), match.end()))

    for match in _MD_TIME_RE.finditer(text):
        if _overlaps(match.start(), match.end()):
            continue
        month, day, hour, minute = (int(part) for part in match.groups())
        spans.append(("md", match.group(0), canonical_md(month, day, hour, minute)))
        consumed.append((match.start(), match.end()))

    for match in _DATE_RE.finditer(text):
        if _overlaps(match.start(), match.end()):
            continue
        year, month, day = (int(part) for part in match.groups())
        spans.append(("date", match.group(0), canonical_date(f"{year:04d}-{month:02d}-{day:02d}")))
        consumed.append((match.start(), match.end()))

    for match in _TIME_RE.finditer(text):
        if _overlaps(match.start(), match.end()):
            continue
        hour, minute = int(match.group(1)), int(match.group(2))
        if hour > 23 or minute > 59:
            continue
        spans.append(("time", match.group(0), canonical_time(hour, minute)))
        consumed.append((match.start(), match.end()))

    return spans


def strip_spans(text: str) -> str:
    """移除所有时间与单号，便于做"剩余数字"扫描。"""
    cleaned = _FULL_DT_RE.sub(" ", text)
    cleaned = _MD_TIME_RE.sub(" ", cleaned)
    cleaned = _DATE_RE.sub(" ", cleaned)
    cleaned = _TIME_RE.sub(" ", cleaned)
    return _ORDER_NO_RE.sub(" ", cleaned)


def order_numbers_in(text: str) -> set[str]:
    return set(_ORDER_NO_RE.findall(text))


def delay_claims_minutes(text: str) -> list[float]:
    """抽取"延误 N 分钟/小时"的口径数字，统一换算成分钟。"""
    claims: list[float] = []
    for pattern in DELAY_PATTERNS:
        for match in pattern.finditer(text):
            groups = match.groups()
            if len(groups) == 2:
                number, unit = groups
            else:
                _, number, unit = groups
            value = float(number)
            if str(unit).lower() in {"小时", "h", "hour"}:
                value *= 60
            claims.append(value)
    return claims


def local_window_ok(value: Any, *, occurred_at: Any, max_hours: int = 48) -> bool:
    """恢复时间必须晚于发生时间且不超过 +48h（§11.2 表 2 T1 校验）。"""
    dt = parse_any_dt(value)
    occurred = parse_any_dt(occurred_at)
    if dt is None or occurred is None:
        return True
    return occurred <= dt <= occurred + timedelta(hours=max_hours)


__all__ = [
    "allowed_datetime_keys",
    "backend_delay_minutes",
    "backend_risk_level",
    "backend_sla_breached",
    "case_of",
    "customer_of",
    "delay_claims_minutes",
    "extract_datetime_spans",
    "latest_message_of",
    "local_short",
    "local_str",
    "local_window_ok",
    "order_numbers_in",
    "order_of",
    "parse_any_dt",
    "risk_of",
    "sla_of",
    "strip_spans",
    "vehicle_of",
]
