"""API 序列化助手（Router 只负责把 ORM/服务结果转成响应模型形状）。

放在 services 目录是为了让 orders/tracking/exceptions 三个 Router 共用同一份形状，
避免字段漂移（前端 types 与 OpenAPI 一致）。
"""

from __future__ import annotations

from typing import Any

from app.models.ai import AiAnalysis, AiAnalysisStep
from app.models.exception import ExceptionCase
from app.models.transport import Order, TrackingEvent
from app.repositories import Repos
from app.services import eta_flow, read_models


def order_out(repos: Repos, order: Order, *, full: bool = False) -> dict[str, Any]:
    view = dict(read_models.order_view(repos, order.id) or {})
    customer = order.customer or repos.customers.get(order.customer_id)
    view.update(
        {
            "customer_level": customer.level if customer else None,
            "cargo_desc": order.cargo_desc,
            "weight_ton": float(order.weight_ton) if order.weight_ton is not None else None,
            "driver_id": order.driver_id,
            "sla_rule_id": order.sla_rule_id,
            # 「预计到达时间」：运营手工登记的独立字段（不进运输轨迹时间线）
            "planned_delivery_at": read_models.iso(order.planned_delivery_at),
            "remark": order.remark,
            "version": order.version,
            "created_at": read_models.iso(order.created_at),
            "updated_at": read_models.iso(order.updated_at),
        }
    )
    case = repos.exceptions.find_open_by_order(order.id)
    view["open_exception"] = (
        {
            "id": case.id,
            "case_no": case.case_no,
            "type": case.type,
            "current_type": eta_flow.current_case_type(case),
            "current_level": eta_flow.current_case_risk(case)[0],
            "current_risk_score": eta_flow.current_case_risk(case)[1],
            "level": case.level,
            "status": case.status,
            "risk_score": case.risk_score,
            "sla_breached": bool(case.sla_breached),
            "sla_delay_minutes": case.sla_delay_minutes,
        }
        if case is not None
        else None
    )
    if full:
        view["sla"] = read_models.sla_view(repos, customer_id=order.customer_id, order_id=order.id)
        latest = repos.tracking.latest(order.id)
        view["last_tracking_at"] = read_models.iso(latest.occurred_at) if latest else None
    return view


def exception_brief(repos: Repos, case: ExceptionCase) -> dict[str, Any]:
    order = case.order or repos.orders.get(case.order_id)
    # 读取时自愈：车辆故障因子按订单车辆现状对齐（未结束的异常；已结束的保持历史判定）
    eta_flow.sync_case_vehicle_factor(repos, case, order)
    root_cause = {"code": case.root_cause_code, "note": case.root_cause_note}
    return {
        "id": case.id,
        "case_no": case.case_no,
        "order_id": case.order_id,
        "customer_id": case.customer_id,
        "vehicle_id": case.vehicle_id,
        "carrier_id": case.carrier_id,
        "type": case.type,
        "current_type": eta_flow.current_case_type(case),
        "current_level": eta_flow.current_case_risk(case)[0],
        "current_risk_score": eta_flow.current_case_risk(case)[1],
        "level": case.level,
        "status": case.status,
        "detected_by": case.detected_by,
        "detection_rule": case.detection_rule,
        "occurred_at": read_models.iso(case.occurred_at),
        "stall_since": read_models.iso(case.stall_since),
        "root_cause": root_cause,
        "impact_summary": case.impact_summary,
        "order_no": order.order_no if order else None,
        "customer_name": case.customer.name if case.customer else None,
        "vehicle_plate": case.vehicle.plate_no if case.vehicle else None,
        "promised_delivery_at": read_models.iso(case.promised_delivery_at),
        "expected_eta_at": read_models.iso(case.expected_eta_at),
        # 实际送达（订单事实）：延误单的 SLA 卡要显示"承诺到达 / 实际送达 / 延误时长"
        "delivered_at": read_models.iso(order.delivered_at) if order else None,
        "current_eta_at": read_models.iso(order.current_eta_at) if order else None,
        "delay_minutes": getattr(case, "delay_minutes", None),
        "sla_delay_minutes": case.sla_delay_minutes,
        "sla_breached": bool(case.sla_breached),
        "risk_score": case.risk_score,
        "risk_factors": case.risk_factors_json or [],
        # 建单时冻结的风险快照（口径 2026-10-08）：建单响应也要带上，否则前端拿到的整单"没有快照"
        "initial_risk_score": case.initial_risk_score,
        "initial_level": case.initial_level,
        "initial_risk_factors": case.initial_risk_factors_json or [],
        "assigned_to": case.assigned_to,
        "resolved_at": read_models.iso(case.resolved_at),
        "closed_at": read_models.iso(case.closed_at),
        "close_reason": case.close_reason,
        "merged_count": case.merged_count,
        "version": case.version,
        "created_at": read_models.iso(case.created_at),
        "updated_at": read_models.iso(case.updated_at),
    }


def tracking_out(
    event: TrackingEvent,
    *,
    current_eta_at: str | None = None,
    exception_id: int | None = None,
) -> dict[str, Any]:
    payload = event.payload_json or {}
    eta = payload.get("_eta") if isinstance(payload, dict) else None
    return {
        "id": event.id,
        "order_id": event.order_id,
        "event_type": event.event_type,
        "city": event.city,
        "address": event.address,
        "occurred_at": read_models.iso(event.occurred_at),
        "source": event.source,
        "speed_kmh": float(event.speed_kmh) if event.speed_kmh is not None else None,
        "payload": payload if isinstance(payload, dict) else None,
        "eta_method": (eta or {}).get("method"),
        "current_eta_at": current_eta_at,
        "exception_id": exception_id,
    }


def analysis_out(analysis: AiAnalysis) -> dict[str, Any]:
    return {
        "id": analysis.id,
        "exception_id": analysis.exception_id,
        "analysis_no": analysis.analysis_no,
        "task_type": analysis.task_type,
        "status": analysis.status,
        "is_replay": bool(analysis.is_replay),
        "model": analysis.model,
        "prompt_version": analysis.prompt_version,
        "risk_level_calculated": analysis.risk_level_calculated,
        "error_code": analysis.error_code,
        "error_message": analysis.error_message,
        "tokens_in": analysis.tokens_in,
        "tokens_out": analysis.tokens_out,
        "latency_ms": analysis.latency_ms,
        "reused_from_id": analysis.reused_from_id,
        "input_hash": analysis.input_hash,
        "started_at": read_models.iso(analysis.started_at),
        "finished_at": read_models.iso(analysis.finished_at),
        "created_at": read_models.iso(analysis.created_at),
        "output": analysis.output_json,
        "steps": [step_out(step) for step in (analysis.steps or [])],
    }


def step_out(step: AiAnalysisStep) -> dict[str, Any]:
    return {
        "step_no": step.step_no,
        "step_type": step.step_type,
        "tool_name": step.tool_name,
        "args": step.args_json,
        "result_summary": step.result_summary,
        "status": step.status,
        "duration_ms": step.duration_ms,
        "error": step.error,
        "created_at": read_models.iso(step.created_at),
    }


__all__ = ["analysis_out", "exception_brief", "order_out", "step_out", "tracking_out"]
