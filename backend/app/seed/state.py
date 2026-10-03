"""Seed 运行上下文：一次 seed 过程中共享的实体、随机源与计数器。"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.clock import to_local
from app.models.auth import User, Workspace
from app.models.master import Carrier, Customer, Driver, SlaRule, Vehicle
from app.models.transport import Order

CASE_NO_PREFIX = "EX"


@dataclass
class SeedContext:
    session: Session
    workspace: Workspace
    now: datetime
    rng: random.Random
    users: dict[str, User] = field(default_factory=dict)
    customers: list[Customer] = field(default_factory=list)
    carriers: list[Carrier] = field(default_factory=list)
    vehicles: list[Vehicle] = field(default_factory=list)
    drivers: list[Driver] = field(default_factory=list)
    sla_rules: list[SlaRule] = field(default_factory=list)
    orders: list[Order] = field(default_factory=list)
    plans: dict[int, dict[str, Any]] = field(default_factory=dict)
    used_order_indices: set[int] = field(default_factory=set)
    counts: dict[str, Any] = field(default_factory=dict)
    case_counters: dict[str, int] = field(default_factory=dict)

    @property
    def workspace_id(self) -> int:
        return self.workspace.id

    def bump(self, key: str, amount: int = 1) -> None:
        self.counts[key] = self.counts.get(key, 0) + amount

    def order_at(self, index: int) -> Order:
        """订单序号（1 起）→ Order，即 SO20260930XXX。

        按**订单号**查找而不是列表下标：精简规模（compact）只生成脚本化案例需要的
        那几张订单，列表不再按序号连续排列，下标取会越界。
        """
        from app.seed.orders import order_no_of

        target = order_no_of(index)
        for order in self.orders:
            if order.order_no == target:
                return order
        raise IndexError(f"订单 {target} 不在本次 seed 范围内（当前规模可能为 compact）")

    def plan_at(self, index: int) -> dict[str, Any]:
        return self.plans[index]

    def next_case_no(self, occurred_at: datetime) -> str:
        """按业务时区（Asia/Shanghai）自然日生成 EXyyyymmddNNN 单号。"""
        local = to_local(occurred_at.replace(tzinfo=UTC) if occurred_at.tzinfo is None else occurred_at)
        day = local.strftime("%Y%m%d")
        self.case_counters[day] = self.case_counters.get(day, 0) + 1
        return f"{CASE_NO_PREFIX}{day}{self.case_counters[day]:03d}"


__all__ = ["CASE_NO_PREFIX", "SeedContext"]
