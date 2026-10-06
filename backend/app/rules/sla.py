"""SLA 规则匹配与违约计算（基线文档 §8.3）。

日历口径：Asia/Shanghai，24×7，不考虑节假日（ADR-A14）。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

from app.models.enums import SlaScopeType

DEFAULT_OFFSET_HOURS = 30
DEFAULT_MAX_DELAY_MINUTES = 30


class SlaRuleLike(Protocol):
    id: int
    name: str
    scope_type: str
    scope_value: str | None
    deadline_offset_hours: int
    max_delay_minutes: int
    priority: int


@dataclass(frozen=True)
class SlaMatch:
    rule_id: int | None
    rule_name: str
    scope_type: str
    scope_value: str | None
    deadline_offset_hours: int
    max_delay_minutes: int

    @property
    def is_default(self) -> bool:
        return self.rule_id is None


@dataclass(frozen=True)
class SlaImpact:
    promised_delivery_at: datetime | None
    expected_eta_at: datetime | None
    delay_minutes: int
    breached: bool
    max_delay_minutes: int
    rule_id: int | None
    rule_name: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "promised_delivery_at": self.promised_delivery_at.isoformat() if self.promised_delivery_at else None,
            "expected_eta_at": self.expected_eta_at.isoformat() if self.expected_eta_at else None,
            "delay_minutes": self.delay_minutes,
            "breached": self.breached,
            "max_delay_minutes": self.max_delay_minutes,
            "rule_id": self.rule_id,
            "rule_name": self.rule_name,
        }


def _naive(dt: datetime | None) -> datetime | None:
    """统一按朴素 UTC 参与计算（DB 里存的就是朴素 UTC）。"""
    if dt is None:
        return None
    return dt.replace(tzinfo=None) if dt.tzinfo is not None else dt


def match_rule(
    rules: Sequence[Any],
    *,
    customer_code: str | None = None,
    customer_level: str | None = None,
    default_offset_hours: int = DEFAULT_OFFSET_HOURS,
    default_max_delay_minutes: int = DEFAULT_MAX_DELAY_MINUTES,
) -> SlaMatch:
    """按 具体客户 > 客户等级 > 默认 的顺序匹配，同级取 priority 最小者。

    2026-10-06 起「具体客户（`CUSTOMER`）」已不在界面/接口开放（只保留「客户等级 VIP」与「默认」），
    这里保留该分支只为兼容历史库里已存在的旧规则；新库不会再产生。
    """
    active = [rule for rule in rules if getattr(rule, "is_active", True)]

    def best(scope_type: SlaScopeType, scope_value: str | None) -> Any | None:
        candidates = [
            rule
            for rule in active
            if str(rule.scope_type) == str(scope_type)
            and (scope_value is None or rule.scope_value == scope_value)
        ]
        if not candidates:
            return None
        return sorted(candidates, key=lambda rule: (getattr(rule, "priority", 100), getattr(rule, "id", 0)))[0]

    picked = None
    if customer_code:
        picked = best(SlaScopeType.CUSTOMER, customer_code)
    if picked is None and customer_level:
        picked = best(SlaScopeType.CUSTOMER_LEVEL, customer_level)
    if picked is None:
        picked = best(SlaScopeType.DEFAULT, None)

    if picked is None:
        return SlaMatch(
            rule_id=None,
            rule_name="内置默认规则",
            scope_type=str(SlaScopeType.DEFAULT),
            scope_value=None,
            deadline_offset_hours=default_offset_hours,
            max_delay_minutes=default_max_delay_minutes,
        )
    return SlaMatch(
        rule_id=getattr(picked, "id", None),
        rule_name=getattr(picked, "name", "未命名规则"),
        scope_type=str(picked.scope_type),
        scope_value=getattr(picked, "scope_value", None),
        deadline_offset_hours=int(picked.deadline_offset_hours),
        max_delay_minutes=int(picked.max_delay_minutes),
    )


def compute_promised_at(dispatched_at: datetime | None, offset_hours: int) -> datetime | None:
    dispatched_at = _naive(dispatched_at)
    if dispatched_at is None:
        return None
    return dispatched_at + timedelta(hours=offset_hours)


def evaluate(match: SlaMatch, *, promised_delivery_at: datetime | None, expected_eta_at: datetime | None) -> SlaImpact:
    promised = _naive(promised_delivery_at)
    expected = _naive(expected_eta_at)
    if promised is None or expected is None:
        delay_minutes = 0
    else:
        delay_minutes = int(round((expected - promised).total_seconds() / 60))
    return SlaImpact(
        promised_delivery_at=promised,
        expected_eta_at=expected,
        delay_minutes=delay_minutes,
        breached=delay_minutes > match.max_delay_minutes,
        max_delay_minutes=match.max_delay_minutes,
        rule_id=match.rule_id,
        rule_name=match.rule_name,
    )
