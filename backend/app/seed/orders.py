"""Seed 订单与轨迹生成（§13.2：1000 单 / 5000+ 轨迹，固定随机种子）。

所有时间都是朴素 UTC（DB 口径），业务展示由前端转 Asia/Shanghai（§7.3 / ADR-A9）。
承诺到达时刻一律由 ``app.rules.sla`` 规则算，不硬编码（§8.3）。
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from app.models.enums import OrderStatus, TrackingEventType
from app.models.master import Carrier, Customer, Driver, SlaRule, Vehicle
from app.models.transport import Order, TrackingEvent
from app.rules import sla as sla_rules
from app.seed import catalog

ORDER_TOTAL = 1000
ORDER_PREFIX = "SO20260930"

# 精简演示规模（默认）：只建脚本化案例真正需要的那几张订单。
# 21 = CASE-A（车辆故障全闭环）、24 = CASE-D1（延误 25min 不违约）、
# 23 = 供 CASE-D2（延误 31min 违约）使用的空闲 IN_TRANSIT 单。
COMPACT_ORDER_INDICES: tuple[int, ...] = (21, 23, 24)

# 状态分布：300 已完成 / 400 运输中（300 IN_TRANSIT + 100 DISPATCHED）/ 300 待发车
BASE_STATUS_COUNTS: dict[str, int] = {
    str(OrderStatus.DELIVERED): 300,
    str(OrderStatus.IN_TRANSIT): 300,
    str(OrderStatus.DISPATCHED): 100,
    str(OrderStatus.CREATED): 300,
}
# 脚本化案例占用的订单序号（1 起）
SCRIPTED_ORDER_INDICES: dict[int, str] = {
    21: str(OrderStatus.IN_TRANSIT),   # CASE-A
    22: str(OrderStatus.DELIVERED),    # CASE-B
    23: str(OrderStatus.IN_TRANSIT),   # CASE-C
    24: str(OrderStatus.IN_TRANSIT),   # CASE-D
    25: str(OrderStatus.IN_TRANSIT),   # CASE-E1
    26: str(OrderStatus.IN_TRANSIT),   # CASE-E2
    27: str(OrderStatus.DISPATCHED),   # CASE-E3
    28: str(OrderStatus.DELIVERED),    # CASE-E4
    29: str(OrderStatus.IN_TRANSIT),   # CASE-E5
}

EVENT_LAYOUT: dict[str, list[str]] = {
    "DELIVERED": [
        str(TrackingEventType.DEPART),
        str(TrackingEventType.ARRIVE),
        str(TrackingEventType.DEPART),
        str(TrackingEventType.STOP),
        str(TrackingEventType.RESUME),
        str(TrackingEventType.ARRIVE),
        str(TrackingEventType.STOP),
        str(TrackingEventType.RESUME),
        str(TrackingEventType.DELIVER),
    ],
    "IN_TRANSIT": [
        str(TrackingEventType.DEPART),
        str(TrackingEventType.ARRIVE),
        str(TrackingEventType.DEPART),
        str(TrackingEventType.STOP),
        str(TrackingEventType.RESUME),
        str(TrackingEventType.ARRIVE),
        str(TrackingEventType.DEPART),
        str(TrackingEventType.STOP),
        str(TrackingEventType.ARRIVE),
    ],
}
EVENT_FRACTIONS: list[float] = catalog.EVENT_FRACTIONS


def order_no_of(index: int) -> str:
    return f"{ORDER_PREFIX}{index:03d}"


def status_plan(rng: random.Random) -> dict[int, str]:
    """构造 1000 个订单的状态：先扣掉脚本化案例占位，再打散填充。"""
    counts = dict(BASE_STATUS_COUNTS)
    statuses: dict[int, str] = {}
    for index, status in SCRIPTED_ORDER_INDICES.items():
        counts[status] -= 1
        statuses[index] = status

    pool: list[str] = []
    for status, count in counts.items():
        pool.extend([status] * max(count, 0))
    rng.shuffle(pool)

    cursor = 0
    for index in range(1, ORDER_TOTAL + 1):
        if index in statuses:
            continue
        statuses[index] = pool[cursor]
        cursor += 1
    return statuses


def _customer_for(index: int, rng: random.Random, customers: list[Customer]) -> Customer:
    if index == 21:
        return next(customer for customer in customers if customer.code == "VIP-01")
    if index == 22:
        return next(customer for customer in customers if customer.code == "VIP-02")
    if index == 23:
        return next(customer for customer in customers if customer.code == "NORM-02")
    if index == 24:
        return next(customer for customer in customers if customer.code == "NORM-04")
    if index in {25, 26, 27, 28, 29}:
        codes = {25: "VIP-03", 26: "VIP-01", 27: "NORM-01", 28: "SVIP-01", 29: "NORM-03"}
        return next(customer for customer in customers if customer.code == codes[index])
    # 权重：NORMAL 多、VIP 少
    weighted: list[Customer] = []
    for customer in customers:
        weighted.extend([customer] * {"SVIP": 1, "VIP": 2, "NORMAL": 5}.get(customer.level, 3))
    return rng.choice(weighted)


def _route_for(index: int, rng: random.Random) -> dict[str, Any]:
    if index == 21:
        return catalog.route_plan(catalog.CASE_A_ROUTE["origin"], catalog.CASE_A_ROUTE["dest"])
    return rng.choice(catalog.ROUTE_PLANS)


def _event_city(plan: dict[str, Any], position: int) -> str:
    waypoints = plan["waypoints"]
    name, _ = waypoints[min(position, len(waypoints) - 1)]
    return name


def build_orders(
    *,
    workspace_id: int,
    now: datetime,
    rng: random.Random,
    customers: list[Customer],
    carriers: list[Carrier],
    vehicles: list[Vehicle],
    drivers: list[Driver],
    sla_rules_list: list[SlaRule],
    indices: tuple[int, ...] | None = None,
) -> tuple[list[Order], dict[int, dict[str, Any]]]:
    """生成订单（含 SLA 快照）与"轨迹计划"，返回 (orders, plans)。

    ``indices`` 为空时生成全部 1000 单（完整规模）；传 ``COMPACT_ORDER_INDICES``
    则只生成脚本化案例需要的那几张（精简演示规模）。
    """
    statuses = status_plan(rng)
    orders: list[Order] = []
    plans: dict[int, dict[str, Any]] = {}
    match_cache: dict[str, Any] = {}

    for index in indices or range(1, ORDER_TOTAL + 1):
        customer = _customer_for(index, rng, customers)
        plan = _route_for(index, rng)
        status = statuses[index]
        carrier = carriers[index % len(carriers)] if index % 5 else rng.choice(carriers)
        vehicle = vehicles[index % len(vehicles)] if index % 7 else rng.choice(vehicles)
        driver = drivers[vehicles.index(vehicle)]
        distance = catalog.route_total_km(plan)
        created_at = now - timedelta(minutes=rng.randrange(0, 7 * 24 * 60))

        if customer.code not in match_cache:
            match_cache[customer.code] = sla_rules.match_rule(
                sla_rules_list, customer_code=customer.code, customer_level=customer.level
            )
        match = match_cache[customer.code]

        cargo = catalog.CARGO_DESCS[rng.randrange(len(catalog.CARGO_DESCS))]
        if index == 21:
            cargo = "汽车配件"
            distance = catalog.CASE_A_TOTAL_KM

        order = Order(
            workspace_id=workspace_id,
            order_no=order_no_of(index),
            customer_id=customer.id,
            carrier_id=carrier.id,
            vehicle_id=vehicle.id,
            driver_id=driver.id,
            origin_city=plan["origin"],
            dest_city=plan["dest"],
            cargo_desc=cargo,
            weight_ton=Decimal(str(round(rng.uniform(3.0, 30.0), 2))),
            distance_km=distance,
            status=status,
            sla_rule_id=match.rule_id,
            remark=None,
            created_at=created_at,
            updated_at=created_at,
        )

        dispatched_at = None
        promised = None
        original_eta = None
        current_eta = None
        delivered_at = None
        last_move_at = None

        if status != str(OrderStatus.CREATED):
            dispatched_at = created_at + timedelta(hours=rng.randrange(1, 6))
            promised = sla_rules.compute_promised_at(dispatched_at, match.deadline_offset_hours)
            original_eta = promised
            if status == str(OrderStatus.DELIVERED):
                delivered_at = promised + timedelta(minutes=rng.randrange(-120, 400))
                current_eta = delivered_at
                last_move_at = delivered_at
            elif status == str(OrderStatus.IN_TRANSIT):
                current_eta = promised + timedelta(minutes=rng.randrange(-60, 300))
                last_move_at = max(
                    dispatched_at + timedelta(minutes=30),
                    now - timedelta(minutes=rng.randrange(5, 800)),
                )
            else:  # DISPATCHED：还没发车
                current_eta = original_eta
                last_move_at = dispatched_at

        order.dispatched_at = dispatched_at
        order.promised_delivery_at = promised
        order.original_eta_at = original_eta
        order.current_eta_at = current_eta
        order.delivered_at = delivered_at

        orders.append(order)
        plans[index] = {
            "status": status,
            "route": plan,
            "dispatched_at": dispatched_at,
            "delivered_at": delivered_at,
            "last_move_at": last_move_at,
            "current_city": _event_city(plan, 2) if last_move_at else None,
            "events": _build_event_specs(
                status=status,
                plan=plan,
                dispatched_at=dispatched_at,
                delivered_at=delivered_at,
                last_move_at=last_move_at,
                rng=rng,
            ),
        }

    return orders, plans


def _build_event_specs(
    *,
    status: str,
    plan: dict[str, Any],
    dispatched_at: datetime | None,
    delivered_at: datetime | None,
    last_move_at: datetime | None,
    rng: random.Random,
) -> list[dict[str, Any]]:
    if dispatched_at is None:
        return []
    if status == str(OrderStatus.DISPATCHED):
        return [
            {
                "event_type": str(TrackingEventType.NOTE),
                "city": plan["origin"],
                "occurred_at": dispatched_at,
                "speed_kmh": None,
                "source": "SYSTEM",
                "payload_json": {"note": "已派车，等待司机发车"},
            }
        ]

    layout = EVENT_LAYOUT[status]
    span = (delivered_at or last_move_at) - dispatched_at  # type: ignore[operator]
    span_minutes = max(int(span.total_seconds() // 60), 1)
    specs: list[dict[str, Any]] = []
    for position, event_type in enumerate(layout):
        fraction = EVENT_FRACTIONS[position]
        occurred_at = dispatched_at + timedelta(minutes=round(span_minutes * fraction))
        if event_type in {str(TrackingEventType.STOP), str(TrackingEventType.NOTE)}:
            speed = Decimal("0.0")
        else:
            speed = Decimal(str(round(rng.uniform(42.0, 78.0), 1)))
        # 途经城市按位置推进：DELIVERED 的最后一站在终点，IN_TRANSIT 的最后一站停在中途
        # （否则"在途订单"的当前位置会显示成目的地，进度比例被算成 1）
        waypoint_position = min(int(fraction * 3), len(plan["waypoints"]) - 2)
        city = plan["waypoints"][waypoint_position][0]
        if position == len(layout) - 1:
            if status == str(OrderStatus.DELIVERED):
                city = plan["waypoints"][-1][0]
            else:
                # 在途：当前位置取路线中点，避免"在途订单已到目的地"
                city = plan["waypoints"][(len(plan["waypoints"]) - 1) // 2][0]
        payload = None
        if position == len(layout) - 1 and status == str(OrderStatus.IN_TRANSIT):
            traveled = catalog.mileage_of(plan, city)
            total = catalog.route_total_km(plan)
            if traveled is not None and total:
                payload = {"progress_ratio": round(traveled / total, 3), "mock": True}
        specs.append(
            {
                "event_type": event_type,
                "city": city,
                "occurred_at": occurred_at,
                "speed_kmh": speed,
                "source": "MOCK" if position % 3 else "DRIVER",
                "payload_json": payload,
            }
        )
    return specs


def build_tracking_events(
    *,
    workspace_id: int,
    orders: list[Order],
    plans: dict[int, dict[str, Any]],
) -> list[TrackingEvent]:
    """按"轨迹计划"生成轨迹事件。

    计划键是**订单序号**（1 起，即 SO20260930XXX 的后三位）。精简规模下序号不连续
    （只建 21/23/24），所以这里按订单号对应订单，不能用列表位置下标。
    """
    by_order_no = {order.order_no: order for order in orders}
    events: list[TrackingEvent] = []
    for index in sorted(plans):
        order = by_order_no.get(order_no_of(index))
        if order is None:
            continue
        for spec in plans[index]["events"]:
            city = spec["city"]
            coords = catalog.CITY_COORDS.get(city)
            events.append(
                TrackingEvent(
                    workspace_id=workspace_id,
                    order_id=order.id,
                    event_type=spec["event_type"],
                    city=city,
                    address=f"{city}物流园",
                    lat=Decimal(str(coords[0])) if coords else None,
                    lng=Decimal(str(coords[1])) if coords else None,
                    occurred_at=spec["occurred_at"],
                    source=spec["source"],
                    speed_kmh=spec["speed_kmh"],
                    payload_json=spec["payload_json"],
                    created_at=spec["occurred_at"],
                )
            )
    return events


__all__ = [
    "BASE_STATUS_COUNTS",
    "EVENT_LAYOUT",
    "ORDER_TOTAL",
    "SCRIPTED_ORDER_INDICES",
    "build_orders",
    "build_tracking_events",
    "order_no_of",
    "status_plan",
]
