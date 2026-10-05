"""只读视图（Read Models）：给 AI Tool 与 Router 用的聚合读模型。

分层约定：AI → Tool → 本模块 → Repository → DB。
本模块只读、不写库、不做鉴权判断（鉴权在 Router 层用 require(Perm) 完成）。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.clock import now_utc
from app.core.masking import mask_email, mask_phone
from app.models.exception import ExceptionCase
from app.repositories import Repos
from app.rules import risk as risk_rules
from app.rules import sla as sla_rules

MAX_TRACKING_EVENTS = 50
HISTORY_DAYS = 90


def iso(dt: datetime | None) -> str | None:
    """DB 里是朴素 UTC，输出统一带 Z。"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def order_view(repos: Repos, order_id: int) -> dict[str, Any] | None:
    order = repos.orders.get(order_id)
    if order is None:
        return None
    return {
        "id": order.id,
        "order_no": order.order_no,
        "status": order.status,
        "customer_id": order.customer_id,
        "customer_name": order.customer.name if order.customer else None,
        "customer_code": order.customer.code if order.customer else None,
        "origin_city": order.origin_city,
        "dest_city": order.dest_city,
        "distance_km": order.distance_km,
        "vehicle_id": order.vehicle_id,
        "vehicle_plate": order.vehicle.plate_no if order.vehicle else None,
        "carrier_id": order.carrier_id,
        "carrier_name": order.carrier.name if order.carrier else None,
        "driver_name": order.driver.name if order.driver else None,
        "dispatched_at": iso(order.dispatched_at),
        "promised_delivery_at": iso(order.promised_delivery_at),
        "original_eta_at": iso(order.original_eta_at),
        "current_eta_at": iso(order.current_eta_at),
        "delivered_at": iso(order.delivered_at),
    }


def tracking_view(
    repos: Repos, order_id: int, limit: int = 20, ascending: bool = False
) -> list[dict[str, Any]]:
    events = (
        repos.tracking.list_for_order_asc(order_id, limit=limit)
        if ascending
        else repos.tracking.list_for_order(order_id, limit=limit)
    )
    return [
        {
            "id": event.id,
            "event_type": event.event_type,
            "city": event.city,
            "address": event.address,
            "occurred_at": iso(event.occurred_at),
            "source": event.source,
            "speed_kmh": float(event.speed_kmh) if event.speed_kmh is not None else None,
        }
        for event in events
    ]


def last_move_at(repos: Repos, order_id: int) -> datetime | None:
    """最后一次"位置变化"的时间：STOP/NOTE 不算移动，视为停滞起点。"""
    for event in repos.tracking.list_for_order(order_id, limit=MAX_TRACKING_EVENTS):
        if event.event_type not in {"STOP", "NOTE"}:
            return event.occurred_at
    return None


def customer_view(repos: Repos, customer_id: int) -> dict[str, Any] | None:
    customer = repos.customers.get(customer_id)
    if customer is None:
        return None
    return {
        "id": customer.id,
        "code": customer.code,
        "name": customer.name,
        "level": customer.level,
        "contact_name": customer.contact_name,
        "contact_phone": mask_phone(customer.contact_phone),
        "contact_email": mask_email(customer.contact_email),
        "notify_pref": customer.notify_pref,
    }


def vehicle_view(repos: Repos, vehicle_id: int | None) -> dict[str, Any] | None:
    if not vehicle_id:
        return None
    vehicle = repos.vehicles.get(vehicle_id)
    if vehicle is None:
        return None
    return {
        "id": vehicle.id,
        "plate_no": vehicle.plate_no,
        "status": vehicle.status,
        "current_city": vehicle.current_city,
        "carrier_id": vehicle.carrier_id,
        "vehicle_type": vehicle.vehicle_type,
    }


def sla_view(repos: Repos, *, customer_id: int | None, order_id: int | None = None) -> dict[str, Any]:
    customer = repos.customers.get(customer_id) if customer_id else None
    order = repos.orders.get(order_id) if order_id else None
    match = sla_rules.match_rule(
        repos.sla_rules.list_active(),
        customer_code=customer.code if customer else None,
        customer_level=customer.level if customer else None,
    )
    promised = order.promised_delivery_at if order else None
    if promised is None and order is not None:
        promised = sla_rules.compute_promised_at(order.dispatched_at, match.deadline_offset_hours)
    expected = order.current_eta_at if order else None
    impact = sla_rules.evaluate(match, promised_delivery_at=promised, expected_eta_at=expected)
    return {
        "rule_id": match.rule_id,
        "rule_name": match.rule_name,
        "scope_type": match.scope_type,
        "scope_value": match.scope_value,
        "deadline_offset_hours": match.deadline_offset_hours,
        "max_delay_minutes": match.max_delay_minutes,
        "promised_delivery_at": iso(promised),
        "expected_eta_at": iso(expected),
        "delay_minutes": impact.delay_minutes,
        "breached": impact.breached,
    }


def exception_history_view(repos: Repos, *, customer_id: int, days: int = HISTORY_DAYS) -> dict[str, Any]:
    since = (now_utc() - timedelta(days=days)).replace(tzinfo=None)
    rows = repos.exceptions.all(
        filters=[ExceptionCase.customer_id == customer_id, ExceptionCase.created_at >= since],
        order_by=[ExceptionCase.id.desc()],
    )
    resolved = [row for row in rows if row.resolved_at is not None and row.created_at is not None]
    durations = [
        int((row.resolved_at - row.created_at).total_seconds() // 60) for row in resolved if row.resolved_at
    ]
    return {
        "days": days,
        "total": len(rows),
        "open": len([row for row in rows if row.status not in {"CLOSED"}]),
        "avg_resolve_minutes": int(sum(durations) / len(durations)) if durations else None,
        "recent": [
            {
                "case_no": row.case_no,
                "type": row.type,
                "level": row.level,
                "status": row.status,
                "closed_reason": row.close_reason,
            }
            for row in rows[:5]
        ],
    }


def exception_facts(repos: Repos, exception_id: int) -> dict[str, Any]:
    """AI 分析与通知生成的事实基线：所有数字都来自这里，LLM 只能复述。"""
    case = repos.exceptions.get(exception_id)
    if case is None:
        return {}
    order = order_view(repos, case.order_id) or {}
    customer = customer_view(repos, case.customer_id) or {}
    sla = sla_view(repos, customer_id=case.customer_id, order_id=case.order_id)
    risk = risk_rules.evaluate_risk(
        delay_minutes=case.sla_delay_minutes if case.sla_delay_minutes is not None else sla["delay_minutes"],
        customer_level=customer.get("level"),
        exception_type=case.type,
    )
    latest_message = repos.messages.latest_for_case(exception_id)
    return {
        "exception": {
            "id": case.id,
            "case_no": case.case_no,
            "type": case.type,
            "level": case.level,
            "status": case.status,
            "occurred_at": iso(case.occurred_at),
            "impact_summary": case.impact_summary,
            "root_cause_code": case.root_cause_code,
            "root_cause_note": case.root_cause_note,
            "expected_eta_at": iso(case.expected_eta_at),
            "promised_delivery_at": iso(case.promised_delivery_at),
            "sla_delay_minutes": case.sla_delay_minutes,
            "sla_breached": bool(case.sla_breached),
            "risk_score": case.risk_score,
            "risk_level": case.level,
        },
        "order": order,
        "customer": customer,
        "sla": sla,
        "vehicle": vehicle_view(repos, case.vehicle_id),
        "risk": {"score": risk.score, "level": risk.level, "factors": risk.factor_dicts},
        "tracking_events": tracking_view(repos, case.order_id, limit=8),
        "latest_carrier_message": (
            {
                "id": latest_message.id,
                "raw_text": latest_message.raw_text,
                "received_at": iso(latest_message.received_at),
                "parse_status": latest_message.parse_status,
                "parse_result": latest_message.parse_result_json,
            }
            if latest_message
            else None
        ),
        "history": exception_history_view(repos, customer_id=case.customer_id),
        "now": iso(now_utc()),
    }
