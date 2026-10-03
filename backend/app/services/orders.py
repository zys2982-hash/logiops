"""订单服务：派车、改基础信息、轨迹写入（同步 ETA 重算 → 异常检测）、送达、取消、自动关闭。

状态机（基线文档 §8.1）全部通过 common.apply_transition 走 plan_transition，禁止裸赋值。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import AppError, ErrorCode, validation_error
from app.models.enums import (
    ActorType,
    AuditSource,
    DriverStatus,
    ExceptionStatus,
    OrderStatus,
    TrackingEventType,
    TrackingSource,
    VehicleStatus,
)
from app.models.master import Vehicle
from app.models.transport import Order, TrackingEvent
from app.repositories import Repos
from app.rules import sla as sla_rules
from app.rules import state_machine
from app.services import detection_flow, eta_flow, read_models
from app.services.common import (
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
            "promised_delivery_at": order.promised_delivery_at,
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
        remark: str | None = None,
        carrier_id: int | None = None,
        vehicle_id: int | None = None,
        driver_id: int | None = None,
    ) -> Order:
        """PATCH /orders/{id}：改基础信息；填 vehicle/carrier 时按状态机派车。"""
        order = self.get(order_id)
        check_version(order, expected_version, "订单")
        before = self._snapshot(order)

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

        vehicle = self.repos.vehicles.get(order.vehicle_id) if order.vehicle_id else None
        if vehicle is not None and str(vehicle.status) == str(VehicleStatus.IDLE):
            vehicle.status = str(VehicleStatus.IN_TRANSIT)
            vehicle.current_city = vehicle.current_city or order.origin_city
            bump_version(vehicle)
            self.repos.vehicles.save(vehicle)
        driver = self.repos.drivers.get(order.driver_id) if order.driver_id else None
        if driver is not None and str(driver.status) == str(DriverStatus.AVAILABLE):
            driver.status = str(DriverStatus.ON_TRIP)
            bump_version(driver)
            self.repos.drivers.save(driver)

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

        # 送达即闭环：PROCESSING → RESOLVED；DETECTED/CONFIRMING/ANALYZING → CLOSED(DELIVERED)
        # 绝不允许"货物已到但异常还挂着"的搁浅状态（§8.1 IN_TRANSIT→DELIVERED 副作用）
        case = self.repos.exceptions.find_open_by_order(order.id)
        if case is not None:
            from app.services.exceptions import ExceptionService  # 局部导入避免循环

            service = ExceptionService(self.repos)
            if state_machine.can_transition(
                state_machine.EntityKind.EXCEPTION, case.status, ExceptionStatus.RESOLVED
            ):
                service.resolve(
                    case.id,
                    note="订单已送达，系统自动解除异常",
                    actor_id=None,
                    expected_version=None,
                    system=True,
                )
            elif state_machine.can_transition(
                state_machine.EntityKind.EXCEPTION, case.status, ExceptionStatus.CLOSED
            ):
                service.close(
                    case.id,
                    reason_code="DELIVERED",
                    note="订单已送达，系统自动归档未处理异常",
                    expected_version=None,
                    actor_id=None,
                    forced=True,
                    system=True,
                )
        return order

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

        if want in {str(OrderStatus.DELIVERED), str(OrderStatus.CLOSED)}:
            if order.vehicle_id:
                vehicle = self.repos.vehicles.get(order.vehicle_id)
                if vehicle is not None and str(vehicle.status) == str(VehicleStatus.IN_TRANSIT):
                    vehicle.status = str(VehicleStatus.IDLE)
                    bump_version(vehicle)
                    self.repos.vehicles.save(vehicle)
            if order.driver_id:
                driver = self.repos.drivers.get(order.driver_id)
                if driver is not None and str(driver.status) != str(DriverStatus.AVAILABLE):
                    driver.status = str(DriverStatus.AVAILABLE)
                    bump_version(driver)
                    self.repos.drivers.save(driver)

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

        for case in self.repos.exceptions.list_open():
            if case.order_id != order.id:
                continue
            from app.services.exceptions import ExceptionService  # 局部导入避免循环

            ExceptionService(self.repos).close(
                case.id,
                reason_code="ORDER_CANCELLED",
                note=reason,
                expected_version=None,
                actor_id=actor_id,
                forced=True,
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
