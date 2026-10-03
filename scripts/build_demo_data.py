"""重建演示数据：订单时间先回填，再按时间线造轨迹/异常 —— 保证"订单 ↔ 异常 ↔ 车辆 ↔ 时间线"全对得上。

为什么要有它（上一版数据的 30 处不一致，都由这三条根因引起）：
1. 旧脚本订单是"现在"创建的，时间线却写在几小时前 → 轨迹/异常早于建单时间。
   现在：先把 order.created_at / dispatched_at 回填到故事起点，再按
   「派车 → 发车 → 途经 → 停滞 → 异常发生」的顺序写入，全部落在建单之后。
2. 旧脚本复用了被别的未结束订单占着的车（同一台车挂两张在途单）。
   现在：每张在途订单独占一台车；建数据前把"仍在在途/维修中"的车归位到空闲。
3. 旧脚本的承诺到达/预计到达是按"创建时刻"算的，回填时间后失效。
   现在：回填 dispatched_at 后**强制**用 SLA 规则重算 promised / original_eta / current_eta。

造完请务必跑一遍自检：`python scripts/audit_demo_consistency.py`（应输出 0 处不一致）。

用法：
  PYTHONPATH=backend backend/.venv/Scripts/python.exe scripts/build_demo_data.py --reset
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import delete, select

from app.db.session import session_scope
from app.models import (
    AiAnalysis,
    AiAnalysisStep,
    Approval,
    AuditLog,
    CarrierMessage,
    Customer,
    ExceptionCase,
    ExceptionEvent,
    FollowupTask,
    Notification,
    Order,
    TrackingEvent,
    Vehicle,
)
from app.models.enums import ExceptionStatus, VehicleStatus
from app.repositories import Repos
from app.rules import sla as sla_rules
from app.rules import state_machine
from app.services import eta_flow
from app.services.common import apply_transition, bump_version, to_naive_utc
from app.services.exceptions import ExceptionService
from app.services.orders import OrderService

BASE = "http://127.0.0.1:8000/api/v1"
MARK = "演示数据 v2"
RESULTS: list[tuple[bool, str]] = []


def check(ok: bool, text: str) -> None:
    RESULTS.append((ok, text))
    print(f"[{'OK  ' if ok else 'FAIL'}] {text}", flush=True)


def wipe() -> None:
    """清空订单与异常（含子表与审计），给重建一个干净起点。"""
    with session_scope() as session:
        order_ids = [row for row in session.scalars(select(Order.id))]
        case_ids = [row for row in session.scalars(select(ExceptionCase.id))]
        analysis_ids = list(session.scalars(select(AiAnalysis.id)))
        if case_ids:
            session.execute(delete(Approval).where(Approval.exception_id.in_(case_ids)))
            for model in (Notification, FollowupTask, ExceptionEvent, CarrierMessage):
                session.execute(delete(model).where(model.exception_id.in_(case_ids)))
            session.execute(delete(AiAnalysis).where(AiAnalysis.exception_id.in_(case_ids)))
        if analysis_ids:
            session.execute(delete(AiAnalysisStep).where(AiAnalysisStep.analysis_id.in_(analysis_ids)))
        if case_ids:
            session.execute(delete(ExceptionCase).where(ExceptionCase.id.in_(case_ids)))
        if order_ids:
            session.execute(delete(CarrierMessage).where(CarrierMessage.order_id.in_(order_ids)))
            session.execute(delete(TrackingEvent).where(TrackingEvent.order_id.in_(order_ids)))
            session.execute(delete(Order).where(Order.id.in_(order_ids)))
        session.execute(
            delete(AuditLog).where(
                ((AuditLog.resource_type == "exception") & AuditLog.resource_id.in_(case_ids or [-1]))
                | (AuditLog.resource_type == "order")
            )
        )
        print(f"  清空：{len(order_ids)} 张订单 / {len(case_ids)} 张异常（含子表与审计）")


def main() -> int:
    if "--reset" not in sys.argv:
        print("加 --reset 才会清理并重建（避免误删）。")
        return 1

    now = datetime.now(UTC).replace(microsecond=0, tzinfo=None)

    def at(minutes_ago: int) -> str:
        return (now - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%S")

    with httpx.Client(base_url=BASE, timeout=30.0) as c:
        token = c.post(
            "/auth/login", json={"email": "owner@logiops.dev", "password": "Demo@12345"}
        ).json()["access_token"]
        h = {"Authorization": f"Bearer {token}", "X-Workspace-Id": "1"}

        wipe()

        # 车辆卫生：订单已清空 → 不再有任何车应该停在"在途 / 维修中"（OFFLINE 是车辆自身状态，保留）
        fixed = []
        with session_scope() as session:
            for vehicle in session.scalars(
                select(Vehicle).where(Vehicle.status.in_([str(VehicleStatus.REPAIRING), str(VehicleStatus.IN_TRANSIT)]))
            ):
                vehicle.status = str(VehicleStatus.IDLE)
                bump_version(vehicle)
                fixed.append(vehicle.plate_no)
        check(True, f"车辆卫生：把 {len(fixed)} 台仍在「在途/维修中」的车归位到空闲 {fixed}")

        customers = {
            item["name"]: item
            for item in c.get("/customers", headers=h, params={"page_size": 50}).json()["items"]
        }
        free_vehicles = [
            v
            for v in c.get("/vehicles", headers=h, params={"page_size": 100}).json()["items"]
            if v["status"] == "IDLE" and v.get("carrier_id")
        ]
        if len(free_vehicles) < 6:
            check(False, f"空闲车辆不足（{len(free_vehicles)} 台），无法保证一单一车")
            return 1
        pools = free_vehicles[:6]

        def create_order(customer: str, origin: str, dest: str, km: int, cargo: str, *, minutes_ago: int) -> dict:
            created = c.post(
                "/orders",
                headers=h,
                json={
                    "customer_id": customers[customer]["id"],
                    "origin_city": origin,
                    "dest_city": dest,
                    "distance_km": km,
                    "cargo_desc": cargo,
                    "remark": MARK,
                },
            ).json()
            # 回填建单时间到故事起点：此后所有轨迹/异常都落在这之后
            with session_scope() as session:
                order = session.get(Order, created["id"])
                order.created_at = now - timedelta(minutes=minutes_ago)
                bump_version(order)
            return c.get(f"/orders/{created['id']}", headers=h).json()

        def dispatch(order: dict, plate: str, *, minutes_ago: int) -> dict:
            vehicle = next(v for v in pools if v["plate_no"] == plate)
            patched = c.patch(
                f"/orders/{order['id']}",
                headers=h,
                json={"carrier_id": vehicle["carrier_id"], "vehicle_id": vehicle["id"]},
            )
            assert patched.status_code == 200, patched.text
            # 回填派车时间，并**强制**按 SLA 规则重算承诺到达 / ETA 快照
            # （不能调 resolve_promised_at：它看到 promised_delivery_at 已有值就直接返回旧值）
            with session_scope() as session:
                repos = Repos(session, workspace_id=1)
                row = repos.orders.get(order["id"])
                row.dispatched_at = now - timedelta(minutes=minutes_ago)
                match, _existing = eta_flow.resolve_promised_at(repos, row)
                promised = sla_rules.compute_promised_at(row.dispatched_at, match.deadline_offset_hours)
                row.promised_delivery_at = promised
                row.original_eta_at = promised
                row.current_eta_at = promised
                bump_version(row)
                order_id = row.id
            return c.get(f"/orders/{order_id}", headers=h).json()

        def track(order_id: int, kind: str, city: str, minutes_ago: int, *, speed: float | None = None,
                  address: str | None = None) -> None:
            with session_scope() as session:
                repos = Repos(session, workspace_id=1)
                OrderService(repos).append_tracking(
                    order_id,
                    event_type=kind,
                    city=city,
                    occurred_at=at(minutes_ago),
                    source="OPERATOR",
                    speed_kmh=speed,
                    address=address,
                    trigger_detection=False,
                )

        def create_case(order_id: int, kind: str, minutes_ago: int, note: str, delay: int | None = None) -> dict:
            body = {
                "order_id": order_id,
                "type": kind,
                "occurred_at": at(minutes_ago),
                "note": note,
            }
            if delay is not None:
                body["delay_minutes"] = delay
            response = c.post("/exceptions", headers=h, json=body)
            assert response.status_code == 201, response.text
            return response.json()

        def confirm(case: dict) -> dict:
            response = c.post(
                f"/exceptions/{case['id']}/confirm",
                headers=h,
                json={"expected_version": case["version"], "note": "值班确认：情况属实，进入跟进"},
            )
            assert response.status_code == 200, response.text
            return response.json()

        # ---------------- 订单 1：待发车（现场演示派车/录轨迹/自动检测） ----------------
        # 注意：这两张放在"最近 1 小时内"，保证它们落在**当地今天**，看板「今日订单」不为 0；
        # 其余订单是跨夜的长期运输故事（真实场景里也常常跨夜），时间线都落在当地昨天 → 趋势图能看到历史。
        o1 = create_order("远洋集团", "天津", "上海", 800, "精密仪器 6.5 吨", minutes_ago=20)
        check(o1["status"] == "CREATED" and not o1["vehicle_id"], f"O1 {o1['order_no']} 待发车（无车辆、无异常）")

        # ---------------- 订单 2：已派车未发车 ----------------
        o2 = create_order("中远海运", "上海", "广州", 1450, "冷链药品 8 吨", minutes_ago=45)
        o2 = dispatch(o2, pools[0]["plate_no"], minutes_ago=35)
        with session_scope() as session:
            vehicle = session.get(Vehicle, o2["vehicle_id"])
            vehicle.status = str(VehicleStatus.IDLE)  # 已派车但未发车 → 车还在待命
        check(o2["status"] == "DISPATCHED", f"O2 {o2['order_no']} 已派车（{pools[0]['plate_no']}，未发车）")

        # ---------------- 订单 3：在途 + 车辆故障（处理中，济南爆胎） ----------------
        o3 = create_order("远洋集团", "天津", "上海", 800, "汽车配件 12 吨", minutes_ago=540)
        o3 = dispatch(o3, pools[1]["plate_no"], minutes_ago=510)
        track(o3["id"], "DEPART", "天津", 480, speed=64)
        track(o3["id"], "NOTE", "德州", 390, speed=58, address="京沪高速德州南服务区")
        track(o3["id"], "STOP", "济南", 240, speed=0, address="京沪高速济南服务区")
        case3 = confirm(create_case(o3["id"], "VEHICLE_BREAKDOWN", 240, "车辆在济南爆胎，已联系修理厂，预计今晚 20:00 恢复"))
        check(
            case3["status"] == "PROCESSING" and "VEHICLE_BREAKDOWN" in [f["code"] for f in case3["risk_factors"] or []],
            f"O3 {o3['order_no']} 在途 + 车辆故障处理中（{pools[1]['plate_no']} 置于维修中）",
        )

        # ---------------- 订单 4：在途 + 延误风险（25 分钟，规则判未违约） ----------------
        o4 = create_order("齐鲁化工", "北京", "郑州", 690, "化工原料 20 吨", minutes_ago=420)
        o4 = dispatch(o4, pools[2]["plate_no"], minutes_ago=390)
        track(o4["id"], "DEPART", "北京", 360, speed=55)
        track(o4["id"], "NOTE", "石家庄", 180, speed=48, address="京港澳高速石家庄段")
        case4 = confirm(create_case(o4["id"], "DELAY_RISK", 180, "京沪高速拥堵，预计延误 25 分钟", delay=25))
        check(
            case4["sla_breached"] is False and int(case4["risk_score"]) == 1,
            f"O4 {o4['order_no']} 延误 25min → 未违约（分数 {case4['risk_score']}，{case4['level']}）",
        )

        # ---------------- 订单 5：在途 + 延误风险（300 分钟，已违约 CRITICAL） ----------------
        o5 = create_order("中远海运", "青岛", "上海", 750, "家电 15 吨", minutes_ago=600)
        o5 = dispatch(o5, pools[3]["plate_no"], minutes_ago=570)
        track(o5["id"], "DEPART", "青岛", 540, speed=60)
        track(o5["id"], "NOTE", "日照", 420, speed=52, address="沈海高速日照段")
        track(o5["id"], "STOP", "连云港", 240, speed=0, address="沈海高速连云港服务区")
        case5 = confirm(create_case(o5["id"], "DELAY_RISK", 300, "青岛港压港，预计延误 300 分钟", delay=300))
        check(
            case5["sla_breached"] is True and case5["level"] == "CRITICAL",
            f"O5 {o5['order_no']} 延误 300min → 违约 CRITICAL（分数 {case5['risk_score']}）",
        )

        # ---------------- 订单 6：已送达 + 车辆故障已解决（送达即闭环） ----------------
        o6 = create_order("远洋集团", "杭州", "南京", 280, "休闲食品 9 吨", minutes_ago=780)
        o6 = dispatch(o6, pools[4]["plate_no"], minutes_ago=750)
        track(o6["id"], "DEPART", "杭州", 720, speed=58)
        track(o6["id"], "STOP", "湖州", 600, speed=0, address="杭宁高速湖州服务区")
        case6 = confirm(create_case(o6["id"], "VEHICLE_BREAKDOWN", 600, "右后轮爆胎，现场更换备胎"))
        with session_scope() as session:
            repos = Repos(session, workspace_id=1)
            OrderService(repos).mark_delivered(o6["id"], occurred_at=at(480), actor_id=None)
        detail6 = c.get(f"/exceptions/{case6['id']}", headers=h).json()
        check(
            detail6["status"] == "RESOLVED",
            f"O6 {o6['order_no']} 已送达 → 异常 RESOLVED（送达即闭环；车辆已释放）",
        )

        # ---------------- 订单 7：在途 + 车辆故障误报已关闭 ----------------
        o7 = create_order("中远海运", "重庆", "贵阳", 480, "机械备件 11 吨", minutes_ago=360)
        o7 = dispatch(o7, pools[5]["plate_no"], minutes_ago=330)
        track(o7["id"], "DEPART", "重庆", 300, speed=50)
        track(o7["id"], "NOTE", "遵义", 240, speed=46, address="兰海高速遵义段")
        case7 = create_case(o7["id"], "VEHICLE_BREAKDOWN", 240, "疑似传动轴异响，现场检查为传感器误报")
        version7 = c.get(f"/exceptions/{case7['id']}", headers=h).json()["version"]
        closed = c.post(
            f"/exceptions/{case7['id']}/close",
            headers=h,
            json={"reason_code": "INVALID", "note": "现场确认为传感器误报，关闭异常", "expected_version": version7},
        )
        check(closed.status_code == 200, f"O7 {o7['order_no']} 误报 → 异常 CLOSED（车辆已释放）")

        print("\n=== 订单 / 异常 对应关系 ===")
        for order in c.get("/orders", headers=h, params={"page_size": 50}).json()["items"]:
            cases = [x for x in c.get(f"/orders/{order['id']}/exceptions", headers=h).json()]
            print(
                f"  {order['order_no']} {order['status']:<11} {order['customer_name']:<6} "
                f"{order['origin_city']}→{order['dest_city']} 车={order.get('vehicle_plate') or '—'} "
                f"异常={[(x['case_no'], x['type'], x['status']) for x in cases] or '无'} "
                f"建单={order['created_at']} 派车={order['dispatched_at']}"
            )

        dashboard = c.get("/dashboard/summary", headers=h).json()
        print(
            f"\n=== 看板自查 ==="
            f"\n  业务日={dashboard['business_date']} 今日订单={dashboard['today_orders']} "
            f"在途(含已派车)={dashboard['in_transit']} 未关闭异常={dashboard['open_exceptions']} "
            f"高风险={dashboard['high_risk']} by_status={dashboard['by_status']}"
        )
        print(
            "  说明：故事窗口最长 14 小时；若现在当地时间是凌晨，部分订单会落在“昨天”，"
            "此时「今日订单」偏小是正常的 —— 建议在白天重建一次。"
        )
        check(dashboard["today_orders"] >= 1, f"看板今日订单 ≥ 1（实际 {dashboard['today_orders']}）")
        check(dashboard["open_exceptions"] >= 3, f"看板未关闭异常 ≥ 3（实际 {dashboard['open_exceptions']}）")

    failed = [text for ok, text in RESULTS if not ok]
    print(f"\n构建完成：{len(RESULTS) - len(failed)}/{len(RESULTS)} 步通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
