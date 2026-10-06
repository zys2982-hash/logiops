"""数据一致性审计：订单 / 异常 / 车辆 / 时间线 / 看板 是否互相对得上。

只读。逐条不变量检查（DB 层 + 接口层交叉核对）并打印问题行；0 处不一致时 exit=0，
可直接当"演示前/造数据后"的检查门。

用法：PYTHONPATH=backend backend/.venv/Scripts/python.exe scripts/audit_demo_consistency.py
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime

from sqlalchemy import select

from app.db.session import session_scope
from app.models import Customer, Order, SlaRule, Vehicle
from app.models.master import Driver
from app.models.exception import ExceptionCase
from app.models.transport import TrackingEvent

OPEN = {"DETECTED", "PROCESSING"}
LIVE_ORDER = {"DISPATCHED", "IN_TRANSIT"}
PROBLEMS: list[str] = []


def bad(text: str) -> None:
    PROBLEMS.append(text)
    print(f"  ✗ {text}")


def main() -> int:
    now = datetime.now(UTC).replace(tzinfo=None)
    with session_scope() as session:
        orders = list(session.scalars(select(Order).order_by(Order.id)))
        cases = list(session.scalars(select(ExceptionCase).order_by(ExceptionCase.id)))
        vehicles = {v.id: v for v in session.scalars(select(Vehicle))}
        drivers = {d.id: d for d in session.scalars(select(Driver))}
        customers = {c.id: c for c in session.scalars(select(Customer))}
        rules = {r.id: r for r in session.scalars(select(SlaRule))}
        tracking = list(session.scalars(select(TrackingEvent).order_by(TrackingEvent.order_id, TrackingEvent.occurred_at)))

        print("=== 数据概览 ===")
        print(f"  订单 {len(orders)} / 异常 {len(cases)} / 车辆 {len(vehicles)}")
        for order in orders:
            vehicle = vehicles.get(order.vehicle_id) if order.vehicle_id else None
            own = [c for c in cases if c.order_id == order.id]
            print(
                f"  od#{order.id} {order.order_no} {order.status:<11} {customers[order.customer_id].name if order.customer_id in customers else '?'} "
                f"{order.origin_city}→{order.dest_city} 车={vehicle.plate_no if vehicle else '—'}"
                f"({vehicle.status if vehicle else '—'}) 异常={[(c.case_no, c.type, c.status) for c in own] or '无'} "
                f"created={order.created_at} dispatched={order.dispatched_at} delivered={order.delivered_at}"
            )

        print("\n=== 不变量检查 ===")

        # 1) 异常必须挂在存在的订单上，且客户 / 车辆 / 承运商与订单一致
        for case in cases:
            order = next((o for o in orders if o.id == case.order_id), None)
            if order is None:
                bad(f"ex#{case.id} {case.case_no} 的订单 #{case.order_id} 不存在")
                continue
            if case.customer_id != order.customer_id:
                bad(f"ex#{case.id} {case.case_no} 客户={case.customer_id} ≠ 订单客户={order.customer_id}")
            if (case.vehicle_id or None) != (order.vehicle_id or None):
                bad(
                    f"ex#{case.id} {case.case_no} 车辆={case.vehicle_id} ≠ 订单车辆={order.vehicle_id}"
                    f"（异常卡片会显示另一台车）"
                )
            if (case.carrier_id or None) != (order.carrier_id or None):
                bad(f"ex#{case.id} {case.case_no} 承运商={case.carrier_id} ≠ 订单承运商={order.carrier_id}")

        # 2) 一台车不能同时服务多张"未结束"订单（DISPATCHED/IN_TRANSIT）
        by_vehicle: dict[int, list[Order]] = {}
        for order in orders:
            if order.status in LIVE_ORDER and order.vehicle_id:
                by_vehicle.setdefault(order.vehicle_id, []).append(order)
        for vehicle_id, group in by_vehicle.items():
            if len(group) > 1:
                bad(
                    f"车辆 {vehicles[vehicle_id].plate_no} 同时挂在 {len(group)} 张未结束订单："
                    f"{[o.order_no for o in group]}"
                )

        # 2b) 车 / 司机状态必须跟着订单（用户口径 2026-10-06）
        #     · 未结束订单（DISPATCHED/IN_TRANSIT）→ 它的车必须 IN_TRANSIT（维修中例外）、司机必须 ON_TRIP
        #     · 反向：ON_TRIP 的司机、IN_TRANSIT 的车必须真的挂着一张未结束订单（不能"卡在出车中"）
        live_orders = [order for order in orders if order.status in LIVE_ORDER]
        live_vehicle_ids = {order.vehicle_id for order in live_orders if order.vehicle_id}
        live_driver_ids = {order.driver_id for order in live_orders if order.driver_id}
        for order in live_orders:
            vehicle = vehicles.get(order.vehicle_id) if order.vehicle_id else None
            if vehicle is not None and str(vehicle.status) not in {"IN_TRANSIT", "REPAIRING"}:
                bad(
                    f"订单 {order.order_no} 未结束（{order.status}），但车 {vehicle.plate_no} "
                    f"状态是 {vehicle.status}（车没跟着订单走）"
                )
            driver = drivers.get(order.driver_id) if order.driver_id else None
            if driver is not None and str(driver.status) != "ON_TRIP":
                bad(
                    f"订单 {order.order_no} 未结束（{order.status}），但司机 {driver.name} "
                    f"状态是 {driver.status}（司机没跟着订单走）"
                )
        for vehicle in vehicles.values():
            if str(vehicle.status) == "IN_TRANSIT" and vehicle.id not in live_vehicle_ids:
                bad(
                    f"车辆 {vehicle.plate_no} 停在 IN_TRANSIT，却没挂任何未结束订单"
                    f"（车卡在在途；跑一次 scripts/sync_crew_status.py --apply 可修）"
                )
        for driver in drivers.values():
            if str(driver.status) == "ON_TRIP" and driver.id not in live_driver_ids:
                bad(
                    f"司机 {driver.name} 停在 ON_TRIP，却没挂任何未结束订单"
                    f"（司机卡在出车中；跑一次 scripts/sync_crew_status.py --apply 可修）"
                )

        # 3) 车辆"维修中"必须有且仅有未结束的车辆故障异常；反之仍计着"车辆故障因子"的未结束单必须让车维修中
        #    （已用「车辆已修复 / 信号级闭环」解除过的单不算持有车辆：因子已移除）
        for vehicle in vehicles.values():
            holding = [
                c for c in cases
                if (c.vehicle_id == vehicle.id or any(o.id == c.order_id and o.vehicle_id == vehicle.id for o in orders))
                and c.type == "VEHICLE_BREAKDOWN"
                and c.status in OPEN
                and "VEHICLE_BREAKDOWN" in {f.get("code") for f in (c.risk_factors_json or [])}
            ]
            if str(vehicle.status) == "REPAIRING" and not holding:
                bad(f"车辆 {vehicle.plate_no} 停在 REPAIRING，却没有仍在计数的未结束车辆故障异常（卡死）")
            for case in holding:
                if str(vehicle.status) != "REPAIRING":
                    bad(
                        f"ex#{case.id} {case.case_no} 仍计着车辆故障因子，但车辆 {vehicle.plate_no} "
                        f"状态是 {vehicle.status}（因子与现状矛盾）"
                    )

        # 4) 订单状态与异常状态的关系
        #    2026-10-06 口径：异常收口只由人工点「解决 / 关闭」，所以送达 / 关闭 / 取消的订单
        #    **允许**继续挂着未结束异常 —— 那正是"等人处置"的正常状态，不再算不一致。
        for case in cases:
            order = next((o for o in orders if o.id == case.order_id), None)
            if order is None:
                continue
            if order.status == "CREATED" and case.status in OPEN:
                bad(f"ex#{case.id} {case.case_no} 未结束，但订单 {order.order_no} 还在「待发车」（未派车就出异常）")
            if order.status == "CREATED" and order.vehicle_id:
                bad(f"订单 {order.order_no} 未派车却已绑定车辆（应在派车时才绑定）")

        # 5) 同一订单**同一问题类型**最多一张未结束异常
        #    2026-10-06：允许"未结束的车辆单 + 未结束的延误单"并存（程序不再替用户收口，
        #    车辆单会一直挂到人工处置），但同一类型不许重复建单。
        for order in orders:
            open_cases = [c for c in cases if c.order_id == order.id and c.status in OPEN]
            by_type: dict[str, list] = {}
            for case in open_cases:
                by_type.setdefault(str(case.type), []).append(case)
            for case_type, group in by_type.items():
                if len(group) > 1:
                    bad(
                        f"订单 {order.order_no} 有 {len(group)} 张未结束的「{case_type}」异常："
                        f"{[c.case_no for c in group]}"
                    )

        # 6) 时间顺序：建单 ≤ 派车 ≤ 发生 ≤（解决/关闭）；不允许未来时间；轨迹在派车之后
        for order in orders:
            if order.dispatched_at and order.dispatched_at < order.created_at:
                bad(f"订单 {order.order_no} 派车时间早于建单时间")
            if order.delivered_at and order.dispatched_at and order.delivered_at < order.dispatched_at:
                bad(f"订单 {order.order_no} 送达时间早于派车时间")
            if order.delivered_at and order.delivered_at > now:
                bad(f"订单 {order.order_no} 送达时间在未来 {order.delivered_at}")
            events = [t for t in tracking if t.order_id == order.id]
            base = order.dispatched_at or order.created_at
            for event in events:
                if event.occurred_at > now:
                    bad(f"订单 {order.order_no} 轨迹 {event.event_type}@{event.city} 发生在未来 {event.occurred_at}")
                if event.occurred_at < order.created_at:
                    bad(f"订单 {order.order_no} 轨迹 {event.event_type}@{event.city} 早于建单时间")
            if events and base and max(e.occurred_at for e in events) < base:
                bad(f"订单 {order.order_no} 轨迹全部早于派车时间")

        # 6b) 新风险模型不变量（2026-10-05；收口口径 2026-10-06）
        #     · 车辆故障单不做 SLA 判定 → 不应有 sla_delay_minutes / sla_breached
        #     · 延误单只在送达后产生 → 订单必须已送达，且延误 = 实际送达 − 承诺送达
        #     · sla_breached 必须与"延误 vs 规则允许延迟"一致（改过送达时间后可合法地翻成 False；
        #       单子不再自动收口，会挂着等人工点「解决 / 关闭」）
        for case in cases:
            order = next((o for o in orders if o.id == case.order_id), None)
            if str(case.type) == "VEHICLE_BREAKDOWN":
                if case.sla_delay_minutes is not None or case.sla_breached:
                    bad(
                        f"ex#{case.id} {case.case_no} 车辆故障单不应有 SLA 判定："
                        f"delay={case.sla_delay_minutes} breached={case.sla_breached}"
                    )
                codes = {str(f.get("code")) for f in (case.risk_factors_json or [])}
                if codes & {"DELAY_BASE", "SLA_BREACH"}:
                    bad(f"ex#{case.id} {case.case_no} 车辆故障单不该带延误/违约因子：{sorted(codes)}")
            if str(case.type) == "DELAY_RISK":
                if order is None or order.delivered_at is None:
                    bad(f"ex#{case.id} {case.case_no} 延误单必须产生于送达之后，但订单未送达")
                    continue
                if order.promised_delivery_at is not None and case.sla_delay_minutes is not None:
                    expected_delay = int(
                        round((order.delivered_at - order.promised_delivery_at).total_seconds() / 60)
                    )
                    if abs(int(case.sla_delay_minutes) - expected_delay) > 1:
                        bad(
                            f"ex#{case.id} {case.case_no} 延误 {case.sla_delay_minutes} ≠ "
                            f"实际送达−承诺送达 {expected_delay}"
                        )
                # sla_breached 必须与"延误 vs 规则允许延迟"一致。2026-10-06 起不能再要求
                # "延误单一直报违约"：改过实际送达时间后它会合法地翻成 False（单子仍挂着或已人工收口）。
                rule = rules.get(order.sla_rule_id) if order.sla_rule_id else None
                if rule is not None and case.sla_delay_minutes is not None:
                    allowed = int(getattr(rule, "max_delay_minutes", 0) or 0)
                    should_breach = int(case.sla_delay_minutes) > allowed
                    if bool(case.sla_breached) != should_breach:
                        bad(
                            f"ex#{case.id} {case.case_no} sla_breached={case.sla_breached} 与规则不一致："
                            f"延误 {case.sla_delay_minutes} 分钟 / 允许 {allowed} 分钟"
                        )
                codes = {str(f.get("code")) for f in (case.risk_factors_json or [])}
                if "SLA_BREACH" in codes:
                    bad(f"ex#{case.id} {case.case_no} 延误单不该再有 SLA_BREACH 因子（新版不重复计违约）")

        for order in orders:
            if order.status != "DELIVERED" or order.delivered_at is None or order.promised_delivery_at is None:
                continue
            delay = int(round((order.delivered_at - order.promised_delivery_at).total_seconds() / 60))
            open_delay = [
                c for c in cases
                if c.order_id == order.id and str(c.type) == "DELAY_RISK" and str(c.status) in OPEN
            ]
            if delay <= 0 and open_delay:
                # 2026-10-06：改过送达时间后"已不再违约"的单不再自动收口，会一直挂着等人工关闭。
                # 允许存在，但不许还挂着「违约」的判定 —— 数字必须跟着事实走。
                still_breached = [c for c in open_delay if c.sla_breached]
                if still_breached:
                    bad(
                        f"订单 {order.order_no} 未超时（提前 {-delay} 分钟）却有未结束延误单仍在报违约："
                        f"{[c.case_no for c in still_breached]}"
                    )

        for case in cases:
            if case.occurred_at > now:
                bad(f"ex#{case.id} {case.case_no} 发生时间在未来 {case.occurred_at}")
            order = next((o for o in orders if o.id == case.order_id), None)
            if order is not None and order.dispatched_at and case.occurred_at < order.created_at:
                bad(f"ex#{case.id} {case.case_no} 发生时间早于订单建单时间")
            if case.resolved_at and case.resolved_at < case.occurred_at:
                bad(f"ex#{case.id} {case.case_no} 解决时间早于发生时间")
            if case.closed_at and case.closed_at < case.occurred_at:
                bad(f"ex#{case.id} {case.case_no} 关闭时间早于发生时间")
            if case.status in OPEN and case.resolved_at is not None:
                bad(f"ex#{case.id} {case.case_no} 未结束却带着 resolved_at")
            if case.status in OPEN and case.closed_at is not None:
                bad(f"ex#{case.id} {case.case_no} 未结束却带着 closed_at")
            if case.status not in OPEN and case.vehicle_status_before is not None:
                bad(f"ex#{case.id} {case.case_no} 已结束却还留着 vehicle_status_before（车辆状态会被卡住）")

        # 7) SLA 快照自洽：承诺 = 派车 + 规则偏移；违约 = 延误 > 允许
        for case in cases:
            order = next((o for o in orders if o.id == case.order_id), None)
            if order is None or order.dispatched_at is None:
                continue
            rule = rules.get(order.sla_rule_id) if order.sla_rule_id else None
            if rule is not None and order.promised_delivery_at is not None:
                expected_promised = order.dispatched_at.replace(microsecond=0)
                from datetime import timedelta

                expected_promised = expected_promised + timedelta(hours=rule.deadline_offset_hours)
                if abs((order.promised_delivery_at - expected_promised).total_seconds()) > 120:
                    bad(
                        f"订单 {order.order_no} 承诺到达 {order.promised_delivery_at} ≠ 派车+{rule.deadline_offset_hours}h "
                        f"({expected_promised})"
                    )
            if case.sla_delay_minutes is not None and rule is not None:
                should_breach = int(case.sla_delay_minutes) > int(rule.max_delay_minutes)
                if bool(case.sla_breached) != should_breach:
                    bad(
                        f"ex#{case.id} {case.case_no} 违约={case.sla_breached}，但延误 {case.sla_delay_minutes} "
                        f"vs 允许 {rule.max_delay_minutes} → 应为 {should_breach}"
                    )
            if case.sla_delay_minutes is not None and case.sla_delay_minutes < -1440:
                bad(f"ex#{case.id} {case.case_no} 延误 {case.sla_delay_minutes} 分钟（超过一天，快照可疑）")

        print(f"\n=== 结论（DB 层）：{len(PROBLEMS)} 处不一致 ===")
        for item in PROBLEMS:
            print(f"  - {item}")

    # ---------- 接口层交叉核对：异常卡上显示的字段必须与该异常所属订单一致 ----------
    print("\n=== 接口层交叉核对（GET /orders、/orders/{id}/exceptions、/exceptions）===")
    try:
        import httpx

        with httpx.Client(base_url="http://127.0.0.1:8000/api/v1", timeout=30) as client:
            token = client.post(
                "/auth/login", json={"email": "owner@logiops.dev", "password": "Demo@12345"}
            ).json()["access_token"]
            headers = {"Authorization": f"Bearer {token}", "X-Workspace-Id": "1"}
            orders = client.get("/orders", headers=headers, params={"page_size": 100}).json()["items"]
            cases = client.get("/exceptions", headers=headers, params={"page_size": 100}).json()["items"]
            by_no = {order["order_no"]: order for order in orders}
            api_problems = 0
            for case in cases:
                order = by_no.get(case.get("order_no"))
                if order is None:
                    bad(f"接口：异常 {case['case_no']} 显示订单号 {case.get('order_no')}，但订单列表里没有这张单")
                    api_problems += 1
                    continue
                if case.get("customer_name") != order.get("customer_name"):
                    bad(
                        f"接口：异常 {case['case_no']} 显示客户 {case.get('customer_name')}，"
                        f"订单 {order['order_no']} 客户是 {order.get('customer_name')}"
                    )
                    api_problems += 1
                if (case.get("vehicle_plate") or None) != (order.get("vehicle_plate") or None):
                    bad(
                        f"接口：异常 {case['case_no']} 显示车牌 {case.get('vehicle_plate')}，"
                        f"订单 {order['order_no']} 车牌是 {order.get('vehicle_plate')}"
                    )
                    api_problems += 1
                if case.get("order_id") != order["id"]:
                    bad(f"接口：异常 {case['case_no']} 的 order_id={case.get('order_id')} 与订单 {order['id']} 不符")
                    api_problems += 1
            for order in orders:
                for case in client.get(f"/orders/{order['id']}/exceptions", headers=headers).json():
                    if case.get("order_no") != order["order_no"] or (
                        case.get("vehicle_plate") or None
                    ) != (order.get("vehicle_plate") or None):
                        bad(
                            f"接口：订单 {order['order_no']} 详情里的异常 {case.get('case_no')} "
                            f"显示 {case.get('order_no')}/{case.get('vehicle_plate')}，与订单不一致"
                        )
                        api_problems += 1
            print(f"  订单 {len(orders)} 张 / 异常 {len(cases)} 张，接口层不一致 {api_problems} 处")

            dashboard = client.get("/dashboard/summary", headers=headers).json()
            print(
                f"  看板：今日订单={dashboard['today_orders']} 在途={dashboard['in_transit']} "
                f"未关闭异常={dashboard['open_exceptions']} 高风险={dashboard['high_risk']} "
                f"by_status={dashboard['by_status']}"
            )
            top = dashboard.get("high_risk_top") or []
            for item in top:
                print(f"    高风险 Top：{item.get('case_no')} {item.get('level')} 分数={item.get('risk_score')}")
    except Exception as exc:  # pragma: no cover - 后端未启动时跳过
        print(f"  （跳过接口核对：{exc}）")

    print(f"\n=== 结论：DB 层 {len(PROBLEMS)} 处不一致 ===")
    return 1 if PROBLEMS else 0


if __name__ == "__main__":
    sys.exit(main())
