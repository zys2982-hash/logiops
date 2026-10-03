"""异常单的可解释性：把「在途信号 → 风险因子 → 分数/等级」这条链讲清楚。

为什么需要它（用户反馈）：
一个订单同时**只有一张**未结束异常单（detection_flow 的"再次命中只合并"策略 +
create_manual 的 OPEN_EXCEPTION_EXISTS 校验），多个在途信号（停滞 / 延误 / 车辆故障 / 违约）
不会各建一行，而是合并进同一张单、以 `risk_factors` 的形式参与定级。
界面只显示因子表时，用户会感觉"在途异常凭空变成了一张异常单、信号不见了"。
所以这里为每个因子列出**证据来源**，并把这张单由哪些信号构成按时间列出来。

只读：全部取自已落库事实（case / order / customer / vehicle / SLA 规则 / 事件 / 轨迹 / 承运商消息），
不引入新计算、不写库、不改变任何既有口径。
"""

from __future__ import annotations

from typing import Any

from app.models.enums import VehicleStatus
from app.repositories import Repos
from app.services import read_models

# 因子 code 与 rules/risk.py::evaluate_risk 一一对应
FACTOR_DELAY = "DELAY_BASE"
FACTOR_VIP = ("CUSTOMER_VIP", "CUSTOMER_SVIP")
FACTOR_VEHICLE = "VEHICLE_BREAKDOWN"
FACTOR_BREACH = "SLA_BREACH"

SIGNAL_LABEL = {
    "DETECTION": "系统检测建单",
    "MERGE": "信号合并",
    "STATUS": "状态流转",
    "MESSAGE": "承运商消息",
    "TRACKING": "轨迹信号",
    "MANUAL_DELAY": "人工录入延误",
}

ONE_CASE_NOTE = (
    "同一订单同时只有一张未结束异常单：多个在途信号（停滞 / 延误 / 车辆故障 / 违约）"
    "不会各建一行，而是合并进这一张单，并在下面的风险因子里体现。"
)


def _minutes(value: Any) -> str:
    return f"{int(value)} 分钟" if value is not None else "—"


def _delay_text(value: Any) -> str:
    """延误分钟的人话：负数表示比承诺早到（"提前"），别显示成"延误 -1845 分钟"。"""
    if value is None:
        return "—"
    minutes = int(value)
    return f"提前 {abs(minutes)} 分钟（未违约）" if minutes < 0 else f"延误 {minutes} 分钟"


def _vehicle_of(repos: Repos, case: Any, order: Any) -> Any:
    if case.vehicle_id:
        vehicle = repos.vehicles.get(case.vehicle_id)
        if vehicle is not None:
            return vehicle
    if order is not None and order.vehicle_id:
        return repos.vehicles.get(order.vehicle_id)
    return None


def _factor_sources(
    repos: Repos, case: Any, order: Any, sla: dict[str, Any], code: str
) -> list[dict[str, Any]]:
    """每个因子"为什么存在"的事实来源（人可核对，不参与计算）。"""
    sources: list[dict[str, Any]] = []
    customer = repos.customers.get(case.customer_id) if case.customer_id else None
    rule_text = (
        f"规则「{sla.get('rule_name')}」允许延误 {_minutes(sla.get('max_delay_minutes'))}"
        if sla.get("rule_name")
        else None
    )

    if code == FACTOR_DELAY:
        if case.delay_minutes is not None:
            sources.append(
                {
                    "kind": "MANUAL_DELAY",
                    "text": f"人工录入延误 {_minutes(case.delay_minutes)}（人工事实，非模型推测）",
                }
            )
        if case.sla_delay_minutes is not None:
            sources.append(
                {
                    "kind": "SLA_SNAPSHOT",
                    "text": f"规则快照：{_delay_text(case.sla_delay_minutes)}",
                }
            )
        if rule_text:
            sources.append({"kind": "SLA_RULE", "text": rule_text})

    elif code in FACTOR_VIP and customer is not None:
        sources.append(
            {
                "kind": "CUSTOMER_LEVEL",
                "text": f"客户 {customer.name}（{customer.code}）等级 {customer.level}",
                "ref": {"customer_id": customer.id},
            }
        )

    elif code == FACTOR_VEHICLE:
        vehicle = _vehicle_of(repos, case, order)
        if vehicle is None:
            sources.append({"kind": "VEHICLE_STATUS", "text": "订单未绑定车辆"})
        else:
            repairing = str(vehicle.status) == str(VehicleStatus.REPAIRING)
            sources.append(
                {
                    "kind": "VEHICLE_STATUS",
                    "text": (
                        f"车辆 {vehicle.plate_no}（#{vehicle.id}）当前状态 {vehicle.status}"
                        + ("→ 仍在维修中，按现状计入" if repairing else "→ 已不在维修中，读取时会自愈移除该因子")
                    ),
                    "ref": {"vehicle_id": vehicle.id},
                }
            )
        if case.root_cause_note:
            sources.append({"kind": "ROOT_CAUSE", "text": f"原因记录：{case.root_cause_note}"})

    elif code == FACTOR_BREACH:
        promised = case.promised_delivery_at
        expected = case.expected_eta_at
        parts = []
        if promised is not None:
            parts.append(f"承诺到达 {read_models.iso(promised)}")
        if expected is not None:
            parts.append(f"预计到达 {read_models.iso(expected)}")
        if case.sla_delay_minutes is not None and sla.get("max_delay_minutes") is not None:
            parts.append(
                f"{_delay_text(case.sla_delay_minutes)} > 允许 {_minutes(sla['max_delay_minutes'])}"
                if case.sla_delay_minutes >= 0
                else _delay_text(case.sla_delay_minutes)
            )
        sources.append({"kind": "SLA_EVAL", "text": "；".join(parts) or "SLA 规则判定为已违约"})

    return sources


def risk_explanation(repos: Repos, case: Any, order: Any = None) -> dict[str, Any]:
    """返回 {factors:[因子+来源], signals:[这张单由哪些信号构成], summary, note}。"""
    order = order if order is not None else repos.orders.get(case.order_id)
    sla = read_models.sla_view(repos, customer_id=case.customer_id, order_id=case.order_id)

    factors = []
    for raw in case.risk_factors_json or []:
        if not isinstance(raw, dict):
            continue
        code = str(raw.get("code"))
        factors.append({**raw, "sources": _factor_sources(repos, case, order, sla, code)})

    signals: list[dict[str, Any]] = []
    events, _total = repos.exception_events.list_for_case(case.id, page_size=30)
    for event in events:
        detail = event.detail_json or {}
        kind = "MERGE" if detail.get("merged") else ("DETECTION" if str(event.event_type) == "DETECTED" else "STATUS")
        text = event.note or f"{event.event_type} {event.from_status or '—'}→{event.to_status or '—'}"
        signals.append(
            {
                "kind": kind,
                "kind_label": SIGNAL_LABEL[kind],
                "at": read_models.iso(event.occurred_at),
                "text": text,
            }
        )

    if case.delay_minutes is not None:
        signals.append(
            {
                "kind": "MANUAL_DELAY",
                "kind_label": SIGNAL_LABEL["MANUAL_DELAY"],
                "at": read_models.iso(case.updated_at),
                "text": f"人工录入延误 {_minutes(case.delay_minutes)}",
            }
        )

    message = repos.messages.latest_for_case(case.id)
    if message is not None:
        signals.append(
            {
                "kind": "MESSAGE",
                "kind_label": SIGNAL_LABEL["MESSAGE"],
                "at": read_models.iso(message.received_at),
                "text": (message.raw_text or "")[:120],
            }
        )

    for item in read_models.tracking_view(repos, case.order_id, limit=8):
        speed = f"，速度 {item.get('speed_kmh')} km/h" if item.get("speed_kmh") is not None else ""
        signals.append(
            {
                "kind": "TRACKING",
                "kind_label": SIGNAL_LABEL["TRACKING"],
                "at": item.get("occurred_at"),
                "text": f"{item.get('event_type')} @ {item.get('city')}{speed}",
            }
        )

    signals.sort(key=lambda item: str(item.get("at") or ""), reverse=True)

    counts: dict[str, int] = {}
    for item in signals:
        counts[item["kind"]] = counts.get(item["kind"], 0) + 1

    return {
        "factors": factors,
        "signals": signals[:20],
        "summary": {
            "signals_total": len(signals),
            "by_kind": counts,
            "merged_count": int(case.merged_count or 0),
            "detected_by": case.detected_by,
            "detection_rule": case.detection_rule,
        },
        "note": ONE_CASE_NOTE,
    }


__all__ = ["ONE_CASE_NOTE", "risk_explanation"]
