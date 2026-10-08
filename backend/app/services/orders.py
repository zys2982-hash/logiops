"""订单服务：派车、改基础信息、轨迹写入（同步 ETA 重算 → 异常检测）、送达、取消、自动关闭。

状态机（基线文档 §8.1）全部通过 common.apply_transition 走 plan_transition，禁止裸赋值。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.core.clock import local_text
from app.core.errors import AppError, ErrorCode, validation_error
from app.models.enums import (
    ActorType,
    AuditSource,
    DetectionRule,
    DriverStatus,
    ExceptionType,
    OrderStatus,
    TrackingEventType,
    TrackingSource,
    VehicleStatus,
)
from app.models.exception import ExceptionCase
from app.models.master import Vehicle
from app.models.transport import Order, TrackingEvent
from app.repositories import Repos
from app.rules import sla as sla_rules
from app.rules import state_machine
from app.services import crew, detection_flow, eta_flow, read_models
from app.services.common import (
    add_event,
    apply_transition,
    bump_version,
    check_version,
    next_order_no,
    now_naive,
    parse_iso_naive,
    require_text,
    to_naive_utc,
    write_audit,
)

ORDER_KIND = state_machine.EntityKind.ORDER


class OrderService:
    """订单域编排。所有方法都在调用方的事务内工作（Router 由 get_db 统一提交）。"""

    def __init__(self, repos: Repos) -> None:
        self.repos = repos
        self.session: Session = repos.session

    # --- 查询 -------------------------------------------------------------
    def get(self, order_id: int) -> Order:
        return self.repos.orders.get_or_404(order_id, "订单不存在")

    def _snapshot(self, order: Order) -> dict[str, Any]:
        return {
            "order_no": order.order_no,
            "status": order.status,
            "carrier_id": order.carrier_id,
            "vehicle_id": order.vehicle_id,
            "driver_id": order.driver_id,
            "dispatched_at": order.dispatched_at,
            "delivered_at": order.delivered_at,
            "promised_delivery_at": order.promised_delivery_at,
            "planned_delivery_at": order.planned_delivery_at,
            "current_eta_at": order.current_eta_at,
        }

    # --- 创建与修改 -------------------------------------------------------
    def create(
        self,
        *,
        customer_id: int,
        origin_city: str,
        dest_city: str,
        order_no: str | None = None,
        cargo_desc: str | None = None,
        weight_ton: Decimal | float | None = None,
        distance_km: int | None = None,
        remark: str | None = None,
        actor_id: int | None = None,
    ) -> Order:
        self.repos.customers.get_or_404(customer_id, "客户不存在")
        number = (order_no or "").strip() or next_order_no(self.repos)
        if self.repos.orders.get_by_no(number) is not None:
            raise AppError(ErrorCode.DUPLICATE_ENTITY, "订单号已存在", {"order_no": number})
        order = Order(
            workspace_id=int(self.repos.workspace_id or 0),
            order_no=number,
            customer_id=customer_id,
            origin_city=require_text(origin_city, "origin_city", max_len=64),
            dest_city=require_text(dest_city, "dest_city", max_len=64),
            cargo_desc=cargo_desc,
            weight_ton=Decimal(str(weight_ton)) if weight_ton is not None else None,
            distance_km=distance_km,
            remark=remark,
            status=str(OrderStatus.CREATED),
        )
        self.repos.orders.add(order)
        write_audit(
            self.session,
            self.repos,
            "order.created",
            resource_type="order",
            resource_id=order.id,
            actor_id=actor_id,
            after=self._snapshot(order),
        )
        return order

    def update_basic(
        self,
        order_id: int,
        *,
        expected_version: int | None = None,
        actor_id: int | None = None,
        origin_city: str | None = None,
        dest_city: str | None = None,
        cargo_desc: str | None = None,
        weight_ton: Decimal | float | None = None,
        distance_km: int | None = None,
        planned_delivery_at: datetime | None = None,
        remark: str | None = None,
        carrier_id: int | None = None,
        vehicle_id: int | None = None,
        driver_id: int | None = None,
    ) -> Order:
        """PATCH /orders/{id}：改基础信息；填 vehicle/carrier 时按状态机派车。"""
        order = self.get(order_id)
        check_version(order, expected_version, "订单")
        before = self._snapshot(order)
        # 改派要能释放"原来的人车"：先记下原绑定（下面可能改 vehicle/driver）
        previous_vehicle_id = order.vehicle_id
        previous_driver_id = order.driver_id

        if order.status in {str(OrderStatus.CLOSED), str(OrderStatus.CANCELLED)}:
            raise AppError(
                ErrorCode.STATE_TRANSITION_INVALID,
                f"订单已 {order.status}，不可再修改",
                {"from": order.status},
            )
        if origin_city is not None:
            order.origin_city = require_text(origin_city, "origin_city", max_len=64)
        if dest_city is not None:
            order.dest_city = require_text(dest_city, "dest_city", max_len=64)
        if cargo_desc is not None:
            order.cargo_desc = cargo_desc
        if weight_ton is not None:
            order.weight_ton = Decimal(str(weight_ton))
        if distance_km is not None:
            if distance_km <= 0:
                raise validation_error("distance_km 必须为正数")
            order.distance_km = distance_km
        if remark is not None:
            order.remark = remark
        if planned_delivery_at is not None:
            # 「预计到达时间」（2026-10-08）：只落这一个字段 —— 不派车、不写轨迹事件、
            # 不触发 ETA 重算与异常检测，也不进运输轨迹时间线（用户明确要求"另一个数据"）。
            # 本次也**不参与延误判定**：要不要用它替代"实际送达"另开改动。
            order.planned_delivery_at = to_naive_utc(planned_delivery_at)
        if driver_id is not None:
            self.repos.drivers.get_or_404(driver_id, "司机不存在")
            order.driver_id = driver_id

        dispatch_target = vehicle_id if vehicle_id is not None else carrier_id
        if dispatch_target is not None:
            if order.status == str(OrderStatus.CREATED):
                order = self.dispatch(
                    order.id,
                    carrier_id=carrier_id if carrier_id is not None else order.carrier_id,
                    vehicle_id=vehicle_id if vehicle_id is not None else order.vehicle_id,
                    driver_id=driver_id,
                    actor_id=actor_id,
                )
            else:
                if carrier_id is not None:
                    self.repos.carriers.get_or_404(carrier_id, "承运商不存在")
                    order.carrier_id = carrier_id
                if vehicle_id is not None:
                    self.repos.vehicles.get_or_404(vehicle_id, "车辆不存在")
                    order.vehicle_id = vehicle_id
                self._apply_vehicle_driver_binding(order)
                bump_version(order)
                self.repos.orders.save(order)

        # 车 / 司机状态随订单走（用户口径 2026-10-06）：
        # · 订单还在途（已派车 / 在途）→ 新车新司机上岗（车回在途、司机出车）；
        # · 换了人车（含"只改司机"这种不走 dispatch 的路径）→ 旧车旧司机放回空闲，
        #   否则旧司机会一直卡在「出车中」（这正是用户反馈的现象）。
        if str(order.status) in crew.LIVE_ORDER_STATUSES:
            crew.occupy(self.repos, order)
        if previous_vehicle_id != order.vehicle_id or previous_driver_id != order.driver_id:
            crew.release(
                self.repos,
                vehicle_id=previous_vehicle_id,
                driver_id=previous_driver_id,
                exclude_order_id=order.id,
            )

        # 延误判定（用户口径 2026-10-08）：改「预计到达时间」→ **立即**按它判定/重算延误单。
        # 放在最后，保证用的是本次改动之后的最终状态（含同一次请求里的派车与承诺时间）。
        if planned_delivery_at is not None:
            self._settle_planned_delay(order, actor_id=actor_id)

        write_audit(
            self.session,
            self.repos,
            "order.updated",
            resource_type="order",
            resource_id=order.id,
            actor_id=actor_id,
            before=before,
            after=self._snapshot(order),
        )
        return order

    # --- 派车 -------------------------------------------------------------
    def _apply_vehicle_driver_binding(self, order: Order, *, auto_bind: bool = True) -> None:
        """派车时的两级约束（业务规则，前端联动只是辅助）：

        **① 归属一致**：车与司机必须属于订单所选承运商；
        **② 强绑定**（项目约定 ADR-A18）：车辆在运营时由其固定主驾驾驶——
          - 车辆已有绑定主驾：`order.driver_id` 必须等于它；未指定则**自动带入**；
          - 车辆未绑定主驾且指定了同承运商司机：**自动建立绑定**（车与司机 1:1）；
          - 车辆未绑定主驾且未指定司机：422（"在用车辆必须有主驾"）。

        直接调 API 也绕不过去：否则会造出"承运商 A + 承运商 B 的车"或
        "津A·12345 的固定主驾是李四、却派给王五"这类矛盾数据。
        """
        if order.carrier_id is None:
            return
        selected_carrier = self.repos.carriers.get(order.carrier_id)
        selected_name = selected_carrier.name if selected_carrier else "—"
        if order.vehicle_id:
            vehicle = self.repos.vehicles.get(order.vehicle_id)
            if (
                vehicle is not None
                and vehicle.carrier_id is not None
                and vehicle.carrier_id != order.carrier_id
            ):
                vehicle_carrier = self.repos.carriers.get(vehicle.carrier_id)
                raise validation_error(
                    "车辆不属于所选承运商（应先选承运商，再选该承运商名下的车辆）",
                    fields=[
                        {
                            "loc": "vehicle_id",
                            "msg": f"车辆 {vehicle.plate_no} 属于承运商 #{vehicle.carrier_id}"
                            f"（{vehicle_carrier.name if vehicle_carrier else '—'}），"
                            f"与所选承运商 #{order.carrier_id}（{selected_name}）不一致",
                        }
                    ],
                )
        if order.driver_id:
            driver = self.repos.drivers.get(order.driver_id)
            if driver is not None and driver.carrier_id is not None and driver.carrier_id != order.carrier_id:
                driver_carrier = self.repos.carriers.get(driver.carrier_id)
                raise validation_error(
                    "司机不属于所选承运商",
                    fields=[
                        {
                            "loc": "driver_id",
                            "msg": f"司机 {driver.name} 属于承运商 #{driver.carrier_id}"
                            f"（{driver_carrier.name if driver_carrier else '—'}），"
                            f"与所选承运商 #{order.carrier_id}（{selected_name}）不一致",
                        }
                    ],
                )

        # ② 车与司机的强绑定
        if not order.vehicle_id:
            return
        vehicle = self.repos.vehicles.get(order.vehicle_id)
        if vehicle is None:
            return
        bound_driver_id = vehicle.current_driver_id
        if bound_driver_id is not None:
            if order.driver_id is None:
                order.driver_id = bound_driver_id  # 自动带入固定主驾（"选了车，司机就是它"）
                return
            if order.driver_id != bound_driver_id:
                bound_driver = self.repos.drivers.get(bound_driver_id)
                raise validation_error(
                    "车辆与司机是固定绑定关系（该车辆在运营时由其固定主驾驾驶）",
                    fields=[
                        {
                            "loc": "driver_id",
                            "msg": f"车辆 {vehicle.plate_no} 的固定主驾是"
                            f"{bound_driver.name if bound_driver else f'#{bound_driver_id}'}，"
                            f"与所选司机不一致；如需换人，请先在「车辆」页修改绑定（或先解除绑定）",
                        }
                    ],
                )
            return

        # 车辆还没有主驾：在用车辆必须有主驾 —— 有司机就建立绑定，没有就拒绝
        if order.driver_id is None:
            raise validation_error(
                "该车辆尚未绑定主驾（在用车辆必须有固定司机）",
                fields=[
                    {
                        "loc": "vehicle_id",
                        "msg": f"车辆 {vehicle.plate_no} 未绑定主驾；请在「车辆」页绑定，"
                        "或在派车时指定该承运商名下的司机（系统会自动建立绑定）",
                    }
                ],
            )
        if auto_bind:
            driver = self.repos.drivers.get(order.driver_id)
            if driver is not None:
                # 一人一车：该司机不能已经绑定在别的车辆上
                # （应用层先拦，否则会落到 vehicle.current_driver_id 唯一索引 → IntegrityError 500）
                bound_elsewhere = [
                    item
                    for item in self.repos.vehicles.all(filters=[Vehicle.current_driver_id == driver.id])
                    if item.id != vehicle.id
                ]
                if bound_elsewhere:
                    raise validation_error(
                        "该司机已绑定其他车辆（一台车只能有一位主驾）",
                        fields=[
                            {
                                "loc": "driver_id",
                                "msg": f"司机 {driver.name} 已绑定车辆 {bound_elsewhere[0].plate_no}；"
                                "请先在「车辆」页解除该车辆的绑定，再为其分配新车",
                            }
                        ],
                    )
                vehicle.current_driver_id = driver.id
                bump_version(vehicle)
                self.repos.vehicles.save(vehicle)

    def dispatch(
        self,
        order_id: int,
        *,
        carrier_id: int | None = None,
        vehicle_id: int | None = None,
        driver_id: int | None = None,
        actor_id: int | None = None,
    ) -> Order:
        order = self.get(order_id)
        plan = state_machine.plan_transition(ORDER_KIND, order.status, OrderStatus.DISPATCHED)
        before = self._snapshot(order)
        # 改派要能释放"原来的人车"：先记下原绑定，绑定新资源后再把旧的放回空闲
        previous_vehicle_id = order.vehicle_id
        previous_driver_id = order.driver_id

        if carrier_id is not None:
            carrier = self.repos.carriers.get_or_404(carrier_id, "承运商不存在")
            order.carrier = carrier
            order.carrier_id = carrier.id
        if vehicle_id is not None:
            vehicle = self.repos.vehicles.get_or_404(vehicle_id, "车辆不存在")
            order.vehicle = vehicle
            order.vehicle_id = vehicle.id
            if order.carrier_id is None:
                order.carrier_id = vehicle.carrier_id
        if driver_id is not None:
            driver = self.repos.drivers.get_or_404(driver_id, "司机不存在")
            order.driver = driver
            order.driver_id = driver.id
        if order.vehicle_id is None and order.carrier_id is None:
            raise validation_error("派车必须提供 carrier_id 或 vehicle_id")
        self._apply_vehicle_driver_binding(order)

        dispatched_at = to_naive_utc(order.dispatched_at) or now_naive()
        match, _ = eta_flow.resolve_promised_at(self.repos, order)
        order.dispatched_at = dispatched_at
        order.promised_delivery_at = sla_rules.compute_promised_at(dispatched_at, match.deadline_offset_hours)
        order.original_eta_at = to_naive_utc(order.original_eta_at) or order.promised_delivery_at
        order.sla_rule_id = match.rule_id
        order.status = plan.to_status
        bump_version(order)
        self.repos.orders.save(order)

        # 车 / 司机状态随订单走（用户口径 2026-10-06）：本单占用的车回在途、司机出车
        crew.occupy(self.repos, order)
        # 改派换掉的"旧车旧司机"要放回空闲，否则会卡在"出车中"（旧代码只设新的、不释放旧的）
        if previous_vehicle_id != order.vehicle_id or previous_driver_id != order.driver_id:
            crew.release(
                self.repos,
                vehicle_id=previous_vehicle_id,
                driver_id=previous_driver_id,
                exclude_order_id=order.id,
            )

        write_audit(
            self.session,
            self.repos,
            "order.dispatched",
            resource_type="order",
            resource_id=order.id,
            actor_id=actor_id,
            before=before,
            after={
                **self._snapshot(order),
                "sla_rule_id": order.sla_rule_id,
                "sla_rule_name": match.rule_name,
                "deadline_offset_hours": match.deadline_offset_hours,
            },
        )
        return order

    # --- ETA --------------------------------------------------------------
    def update_eta(
        self,
        order_id: int,
        *,
        eta_at: datetime | str | None,
        reason: str,
        actor_id: int | None = None,
        source: str = AuditSource.MANUAL,
        apply_to_case: bool = True,
    ) -> Order:
        order = self.get(order_id)
        before = self._snapshot(order)
        target = parse_iso_naive(eta_at)
        if target is None:
            raise validation_error("eta_at 必须为合法时间")
        order.current_eta_at = target
        bump_version(order)
        self.repos.orders.save(order)

        case = self.repos.exceptions.find_open_by_order(order.id) if apply_to_case else None
        if case is not None:
            eta_flow.refresh_case_impact(self.repos, case, order, eta_at=target)

        write_audit(
            self.session,
            self.repos,
            "order.eta_updated",
            resource_type="order",
            resource_id=order.id,
            actor_id=actor_id,
            before=before,
            after={**self._snapshot(order), "reason": reason},
            source=source,
        )
        return order

    # --- 轨迹 -------------------------------------------------------------
    def append_tracking(
        self,
        order_id: int,
        *,
        event_type: str,
        city: str,
        occurred_at: datetime | str | None = None,
        source: str = TrackingSource.MOCK,
        speed_kmh: float | None = None,
        address: str | None = None,
        payload: dict[str, Any] | None = None,
        actor_id: int | None = None,
        trigger_detection: bool = True,
    ) -> TrackingEvent:
        """写入轨迹 → 同步 ETA 重算 → 异常检测（§8.4 触发时机）。"""
        order = self.get(order_id)
        if str(order.status) not in {
            str(OrderStatus.CREATED),
            str(OrderStatus.DISPATCHED),
            str(OrderStatus.IN_TRANSIT),
        }:
            raise AppError(
                ErrorCode.STATE_TRANSITION_INVALID,
                f"订单已 {order.status}，不能追加轨迹",
                {"from": order.status},
            )
        kind = str(event_type or "").upper()
        if kind not in {member.value for member in TrackingEventType}:
            raise validation_error("event_type 非法", fields=[{"loc": "event_type", "msg": kind}])
        if str(source).upper() not in {member.value for member in TrackingSource}:
            raise validation_error("source 非法", fields=[{"loc": "source", "msg": str(source)}])

        moment = parse_iso_naive(occurred_at) or now_naive()
        event = TrackingEvent(
            workspace_id=int(self.repos.workspace_id or 0),
            order_id=order.id,
            event_type=kind,
            city=require_text(city, "city", max_len=64),
            address=address,
            occurred_at=moment,
            source=str(source).upper(),
            speed_kmh=Decimal(str(speed_kmh)) if speed_kmh is not None else None,
            payload_json=payload,
        )
        self.repos.tracking.add(event)

        if kind == str(TrackingEventType.DEPART) and order.status == str(OrderStatus.DISPATCHED):
            apply_transition(order, ORDER_KIND, OrderStatus.IN_TRANSIT)
            order.current_eta_at = to_naive_utc(order.current_eta_at) or to_naive_utc(order.original_eta_at)
            bump_version(order)
            self.repos.orders.save(order)

        if order.vehicle_id:
            vehicle = self.repos.vehicles.get(order.vehicle_id)
            if vehicle is not None:
                if kind == str(TrackingEventType.REPAIR_START):
                    vehicle.status = str(VehicleStatus.REPAIRING)
                elif kind == str(TrackingEventType.REPAIR_END):
                    vehicle.status = str(VehicleStatus.IN_TRANSIT)
                vehicle.current_city = event.city
                bump_version(vehicle)
                self.repos.vehicles.save(vehicle)

        write_audit(
            self.session,
            self.repos,
            "order.tracking_appended",
            resource_type="order",
            resource_id=order.id,
            actor_id=actor_id,
            after={
                "tracking_event_id": event.id,
                "event_type": kind,
                "city": event.city,
                "occurred_at": event.occurred_at,
                "source": event.source,
            },
        )

        if kind == str(TrackingEventType.DELIVER):
            self.mark_delivered(order.id, occurred_at=moment, actor_id=actor_id)
            return event

        eta_info = self.recalc_eta(order, actor_id=actor_id, source=AuditSource.SYSTEM)
        # 把本次 ETA 口径留在轨迹 payload 上，供前端时间线与单测断言 eta_method
        event.payload_json = {**(payload or {}), "_eta": eta_info}
        self.repos.tracking.save(event)
        if trigger_detection:
            detection_flow.detect_for_order(self.repos, self.get(order.id), moment=moment, actor_id=actor_id)
        return event

    def recalc_eta(
        self,
        order: Order,
        *,
        repair_recovery_at: datetime | None = None,
        actor_id: int | None = None,
        source: str = AuditSource.SYSTEM,
    ) -> dict[str, Any]:
        before = self._snapshot(order)
        result = eta_flow.recalc_order_eta(self.repos, order, repair_recovery_at=repair_recovery_at)
        write_audit(
            self.session,
            self.repos,
            "order.eta_recalculated",
            resource_type="order",
            resource_id=order.id,
            actor_type=ActorType.SYSTEM,
            actor_id=actor_id,
            before=before,
            after={**self._snapshot(order), "eta_method": result.method, "remaining_km": result.remaining_km},
            source=source,
        )
        return result.as_dict()

    # --- 送达与关闭 -------------------------------------------------------
    def mark_delivered(
        self,
        order_id: int,
        *,
        occurred_at: datetime | str | None = None,
        actor_id: int | None = None,
    ) -> Order:
        order = self.get(order_id)
        plan = state_machine.plan_transition(ORDER_KIND, order.status, OrderStatus.DELIVERED)
        before = self._snapshot(order)
        order.status = plan.to_status
        order.delivered_at = parse_iso_naive(occurred_at) or now_naive()
        order.current_eta_at = order.delivered_at
        bump_version(order)
        self.repos.orders.save(order)

        # 车辆：订单跑完就放回 IDLE（原口径不变）。注意这**不会**收口还开着的车辆故障单——
        # 单子仍挂着等你点「解决 / 关闭」（2026-10-06 口径）；车已上路，"车辆故障"因子按现状
        # 自然消失（信号级闭环），所以那张单的当前风险会变成 0，这不是错，是"按现状算"。
        if order.vehicle_id:
            vehicle = self.repos.vehicles.get(order.vehicle_id)
            if vehicle is not None:
                vehicle.status = str(VehicleStatus.IDLE)
                bump_version(vehicle)
                self.repos.vehicles.save(vehicle)
        if order.driver_id:
            driver = self.repos.drivers.get(order.driver_id)
            if driver is not None:
                driver.status = str(DriverStatus.AVAILABLE)
                bump_version(driver)
                self.repos.drivers.save(driver)

        write_audit(
            self.session,
            self.repos,
            "order.delivered",
            resource_type="order",
            resource_id=order.id,
            actor_id=actor_id,
            before=before,
            after=self._snapshot(order),
            source=AuditSource.SYSTEM if actor_id is None else AuditSource.MANUAL,
        )

        # 送达**不再自动收口异常**（用户口径 2026-10-06）：已解决 / 已关闭只能由人工在异常页或
        # 订单页点「解决 / 关闭」触发。送达也**不再建延误单/重算延误**（2026-10-08 起延误改由
        # 「预计到达时间」驱动，见 `update_basic` → `_settle_planned_delay`），这里只做车辆故障因子对齐。
        #
        # 顺手把「车辆故障」因子对齐现状（比如上面因为车单还没收口而保留了 REPAIRING，
        # 或车辆此前已被别的路径改过状态）。读取时本来也会自愈，这里先落库，DB 层自检才一致。
        for case in self.repos.exceptions.list_open_by_order(order.id):
            eta_flow.sync_case_vehicle_factor(self.repos, case, order)

        # 延误判定已改由「预计到达时间」驱动（2026-10-08 用户口径）：**送达不再触发延误结算**，
        # 判定发生在"保存 / 修改预计到达时间"时（见 update_basic → _settle_planned_delay）。
        return order

    def _settle_planned_delay(self, order: Order, *, actor_id: int | None) -> ExceptionCase | None:
        """按「预计到达时间」结算延误（**用户口径 2026-10-08**）。

        与原口径（2026-10-05：送达时按**实际送达**判定）的差异：

        |  | 原口径 | 现口径 |
        |---|---|---|
        | 判定时点 | `order.delivered_at` | `order.planned_delivery_at` |
        | 触发点 | 订单送达 / 修正实际送达 | **保存或修改预计到达时间** |
        | 没填预计到达 | —— | **不判定**（用户选择，不回落到实际送达） |

        · 预计到达 − 承诺送达 > 规则允许延迟 → 自动建「延误异常单」（DETECTED 待确认）；
        · 未超 → 不建单；
        · 已存在未结束的延误单 → 只重算延误与分数，**不自动收口**（解决 / 关闭永远由人工点）。

        注意：判定**不要求订单已送达**——用户口径是"保存预送达时就判定"。
        """
        planned = to_naive_utc(order.planned_delivery_at)
        if planned is None:
            return None
        match, promised = eta_flow.resolve_promised_at(self.repos, order)
        if promised is None:
            return None
        delay = int(round((planned - promised).total_seconds() / 60))
        breached = delay > int(match.max_delay_minutes)
        # 按类型找：2026-10-06 起同一订单允许"未结束的车辆单 + 未结束的延误单"并存（各管一个问题），
        # 所以这里必须精确找**延误**单，不能拿"最新那张未结束单"顶替。
        existing = self.repos.exceptions.find_open_by_order_and_type(
            order.id, str(ExceptionType.DELAY_RISK)
        )

        if existing is not None:
            # 预计到达被改了 → 重算（允许升也允许降：改的就是算错的那个值）
            eta_flow.refresh_case_impact(
                self.repos, existing, order, eta_at=planned, allow_downgrade=True
            )
            # 无论是否仍违约都**不自动收口**（用户口径 2026-10-06）：单子留给人工点「解决 / 关闭」，
            # 程序只把重算后的事实写进时间线，避免"系统悄悄结束一张单"。
            add_event(
                self.repos.session,
                self.repos,
                exception_id=existing.id,
                event_type="ETA_UPDATED",
                from_status=existing.status,
                to_status=existing.status,
                actor_type=ActorType.SYSTEM,
                actor_id=actor_id,
                note=(
                    f"按预计到达时间重算：延误 {delay} 分钟"
                    f"（允许 {match.max_delay_minutes} 分钟）"
                    + ("" if breached else "，已不再违约；是否收口由人工决定")
                ),
                detail={
                    "planned_delivery_at": str(planned),
                    "delay_minutes": delay,
                    "breached": breached,
                    "auto_closed": False,
                },
            )
            return existing

        if breached:
            return self._open_planned_breach_case(
                order,
                match=match,
                promised=promised,
                planned=planned,
                delay=delay,
                actor_id=actor_id,
            )
        return None

    def update_delivered_at(
        self,
        order_id: int,
        *,
        delivered_at: datetime | str,
        note: str | None = None,
        actor_id: int | None = None,
    ) -> Order:
        """修正**实际送达时间**（送达时间录错时用；订单口径 2026-10-05）。

        只允许在订单已送达（DELIVERED）后修正。**只改订单事实**（`delivered_at` / `current_eta_at` + 审计）：
        延误判定自 2026-10-08 起改用「预计到达时间」，**不再受这里影响** ——
        要动延误就去改订单页 / 异常页的「预计到达时间」。
        """
        order = self.get(order_id)
        if str(order.status) != str(OrderStatus.DELIVERED):
            raise AppError(
                ErrorCode.STATE_TRANSITION_INVALID,
                "订单尚未送达，实际送达时间还不存在（修正实际送达只在送达后可用）",
                {"status": str(order.status)},
            )
        moment = parse_iso_naive(delivered_at)
        if moment is None:
            raise validation_error("delivered_at 必填且格式合法")
        if order.dispatched_at and moment < order.dispatched_at:
            raise validation_error("实际送达时间不能早于派车时间")
        note_text = str(note).strip()[:500] if note else None

        before = self._snapshot(order)
        order.delivered_at = moment
        order.current_eta_at = moment
        bump_version(order)
        self.repos.orders.save(order)
        write_audit(
            self.session,
            self.repos,
            "order.delivered_at_corrected",
            resource_type="order",
            resource_id=order.id,
            actor_id=actor_id,
            before=before,
            after={**self._snapshot(order), "note": note_text},
        )
        # 延误判定已改用「预计到达时间」（2026-10-08）：修正实际送达**不再重算延误单**，
        # 只留审计与事实。要改延误请去改「预计到达时间」。
        return order

    def _open_planned_breach_case(
        self,
        order: Order,
        *,
        match: Any,
        promised: datetime,
        planned: datetime,
        delay: int,
        actor_id: int | None,
    ) -> ExceptionCase | None:
        """预计到达超时 → 自动建「延误异常单」（DETECTED 待确认，detection_rule=DELIVERED_BREACH）。

        注：`detection_rule` 沿用既有枚举值 `DELIVERED_BREACH`（历史数据也在用它，改名会动到
        存量数据的语义），但它现在表示"**预计到达**超时建单"。
        """
        from app.services import detection_flow  # 局部导入避免循环

        # 已有未结束的**延误**单就不再叠加（同一个问题只一张单，按类型精确查）；
        # 其它类型（车辆故障等）的未结束单不阻挡延误单 —— 2026-10-06 起允许两者并存，
        # 因为程序不再替用户收口，车辆单会一直挂到人工处置为止。
        open_delay = self.repos.exceptions.find_open_by_order_and_type(
            order.id, str(ExceptionType.DELAY_RISK)
        )
        if open_delay is not None:
            return None

        case = detection_flow.create_case_record(
            self.repos,
            order,
            exception_type=str(ExceptionType.DELAY_RISK),
            occurred_at=planned,
            detection_rule=str(DetectionRule.DELIVERED_BREACH),
            detected_by="SYSTEM",
            moment=planned,
            actor_id=actor_id,
        )
        # 摘要整串落库、前端原样显示 → 时间必须在这里就转成业务时区（否则同屏出现 UTC）
        case.impact_summary = (
            f"预计到达超时：预计到达 {local_text(planned)}，"
            f"承诺 {local_text(promised)}，延误 {delay} 分钟"
            f"（{match.rule_name} 允许 {match.max_delay_minutes} 分钟）"
        )
        add_event(
            self.repos.session,
            self.repos,
            exception_id=case.id,
            event_type="DETECTED",
            from_status=None,
            to_status=case.status,
            actor_type=ActorType.SYSTEM,
            actor_id=actor_id,
            note=(
                f"按预计到达时间判定违约：预计到达 − 承诺送达 = {delay} 分钟 > "
                f"允许 {match.max_delay_minutes} 分钟（{match.rule_name}）"
            ),
            detail={
                "rule": str(DetectionRule.DELIVERED_BREACH),
                "planned_delivery_at": str(planned),
                "promised_delivery_at": str(promised),
                "delay_minutes": delay,
                "max_delay_minutes": match.max_delay_minutes,
            },
        )
        write_audit(
            self.session,
            self.repos,
            "exception.detected",
            resource_type="exception",
            resource_id=case.id,
            actor_type=ActorType.SYSTEM,
            actor_id=actor_id,
            after={
                "case_no": case.case_no,
                "order_id": order.id,
                "type": str(case.type),
                "rule": str(DetectionRule.DELIVERED_BREACH),
                "delay_minutes": delay,
                "level": str(case.level),
                "risk_score": case.risk_score,
            },
        )
        self.repos.exceptions.save(case)
        return case

    def force_status(
        self,
        order_id: int,
        target: str,
        *,
        note: str | None = None,
        actor_id: int | None = None,
    ) -> Order:
        """演示/管理用途：**直接设定**订单状态，跳过状态机（仍写审计，可追溯）。

        用户口径："演示时订单状态要能自己选"。与 mark_delivered / close_order 的区别是不做合法性
        拦截（允许 DELIVERED → IN_TRANSIT 这类回退），但保证字段自洽：

        - 进入 IN_TRANSIT / DELIVERED 且此前未派车：补 dispatched_at；
        - 进入 DELIVERED：补 delivered_at，并把 current_eta_at 锁到送达时刻（与 mark_delivered 同口径）；
        - 回到未送达状态（CREATED / DISPATCHED / IN_TRANSIT）：清 delivered_at；
        - 进入 DELIVERED / CLOSED：车辆仍在途则释放为 IDLE、司机置 AVAILABLE（与 mark_delivered 一致）；
        - 写 ``order.status_forced`` 审计，note 一并留痕。

        注意：不级联改异常单状态（订单与异常可分别直设，演示需要）。
        """
        order = self.get(order_id)
        want = str(target or "").strip().upper()
        if want not in {member.value for member in OrderStatus}:
            raise validation_error(
                "订单状态非法",
                fields=[{"loc": "status", "msg": f"{want} 不在 {[m.value for m in OrderStatus]}"}],
            )
        before = self._snapshot(order)
        if str(order.status) == want:
            return order

        moment = now_naive()
        if want in {str(OrderStatus.IN_TRANSIT), str(OrderStatus.DELIVERED)} and order.dispatched_at is None:
            order.dispatched_at = moment
        if want == str(OrderStatus.DELIVERED):
            order.delivered_at = order.delivered_at or moment
            order.current_eta_at = order.delivered_at
        elif want in {
            str(OrderStatus.CREATED),
            str(OrderStatus.DISPATCHED),
            str(OrderStatus.IN_TRANSIT),
        }:
            order.delivered_at = None
        order.status = want
        bump_version(order)
        self.repos.orders.save(order)

        # 车 / 司机状态跟着订单走（用户口径 2026-10-06）：原来只处理"送达/关闭时释放"，
        # 直设成「已发车 / 在途」时车与司机纹丝不动 —— 现在两个方向都同步：
        # 在途（DISPATCHED / IN_TRANSIT）就上岗，其余（待发车 / 送达 / 关闭 / 取消）就释放。
        if want in crew.LIVE_ORDER_STATUSES:
            crew.occupy(self.repos, order)
        else:
            crew.release(
                self.repos,
                vehicle_id=order.vehicle_id,
                driver_id=order.driver_id,
                exclude_order_id=order.id,
            )

        write_audit(
            self.session,
            self.repos,
            "order.status_forced",
            resource_type="order",
            resource_id=order.id,
            actor_type=ActorType.SYSTEM if actor_id is None else ActorType.USER,
            actor_id=actor_id,
            before=before,
            after={**self._snapshot(order), "note": note},
            source=AuditSource.SYSTEM if actor_id is None else AuditSource.MANUAL,
        )
        return order

    def close_order(
        self,
        order_id: int,
        *,
        reason: str = "24 小时未更新，系统自动归档",
        actor_id: int | None = None,
    ) -> Order:
        order = self.get(order_id)
        plan = state_machine.plan_transition(ORDER_KIND, order.status, OrderStatus.CLOSED, reason)
        before = self._snapshot(order)
        order.status = plan.to_status
        bump_version(order)
        self.repos.orders.save(order)
        write_audit(
            self.session,
            self.repos,
            "order.closed",
            resource_type="order",
            resource_id=order.id,
            actor_id=actor_id,
            before=before,
            after={**self._snapshot(order), "reason": reason},
            source=AuditSource.SYSTEM,
        )
        return order

    def cancel(
        self,
        order_id: int,
        *,
        reason: str,
        actor_id: int | None = None,
    ) -> Order:
        order = self.get(order_id)
        plan = state_machine.plan_transition(ORDER_KIND, order.status, OrderStatus.CANCELLED, reason)
        before = self._snapshot(order)
        order.status = plan.to_status
        bump_version(order)
        self.repos.orders.save(order)

        # 取消订单**不再连带关闭**它的异常单（用户口径 2026-10-06：异常状态只由人工在异常页点）。
        # 订单没了、异常单还开着是允许的 —— 由人来决定它是解决还是关闭。
        # 但车与司机要放回空闲（订单行程结束了，人车不该继续占着）。
        crew.release(
            self.repos,
            vehicle_id=order.vehicle_id,
            driver_id=order.driver_id,
            exclude_order_id=order.id,
        )
        write_audit(
            self.session,
            self.repos,
            "order.cancelled",
            resource_type="order",
            resource_id=order.id,
            actor_id=actor_id,
            before=before,
            after={**self._snapshot(order), "reason": reason},
        )
        return order

    # --- 只读视图 ---------------------------------------------------------
    def detail(self, order_id: int) -> dict[str, Any]:
        order = self.get(order_id)
        view = dict(read_models.order_view(self.repos, order.id) or {})
        view.update(
            {
                "cargo_desc": order.cargo_desc,
                "weight_ton": float(order.weight_ton) if order.weight_ton is not None else None,
                "driver_id": order.driver_id,
                "sla_rule_id": order.sla_rule_id,
                "remark": order.remark,
                "version": order.version,
                "created_at": read_models.iso(order.created_at),
                "updated_at": read_models.iso(order.updated_at),
            }
        )
        case = self.repos.exceptions.find_open_by_order(order.id)
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
        view["sla"] = read_models.sla_view(self.repos, customer_id=order.customer_id, order_id=order.id)
        latest = self.repos.tracking.latest(order.id)
        view["last_tracking_at"] = read_models.iso(latest.occurred_at) if latest else None
        return view

    def auto_close_delivered(self, *, hours: int = 24, moment: datetime | None = None) -> list[int]:
        now = to_naive_utc(moment) or now_naive()
        threshold = now - timedelta(hours=hours)
        closed: list[int] = []
        for order in self.repos.orders.all(filters=[Order.status == str(OrderStatus.DELIVERED)]):
            delivered_at = to_naive_utc(order.delivered_at)
            if delivered_at is not None and delivered_at <= threshold:
                self.close_order(order.id, reason=f"送达后 {hours} 小时自动归档")
                closed.append(order.id)
        return closed

    def list_for_customer(self, customer_id: int, *, limit: int = 20) -> list[Order]:
        orders, _ = self.repos.orders.list(
            filters=[Order.customer_id == customer_id],
            order_by=[Order.id.desc()],
            page=1,
            page_size=limit,
        )
        return orders


__all__ = ["OrderService"]
