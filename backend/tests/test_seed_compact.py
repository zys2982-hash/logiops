"""演示数据规模：compact（默认，每种异常类型一单）与 full（1000 单）契约（Lead 维护）。

用户反馈"订单数太乱太多"，默认规模改为 compact：
  3 订单 = CASE-A（车辆故障全闭环）+ CASE-D1/D2（送达 25min 未违约 / 31min 违约的边界两侧）
  2 异常 = CASE-A（VEHICLE_BREAKDOWN）+ CASE-D2（DELAY_RISK，送达结算自动建单）；
  CASE-D1 是**负样本**：送达未超允许延迟 → 不建单（2026-10-05 新模型）
- full 规模（1000 单 / 50 异常）保留，供分页、统计、压测类演示使用。
"""

from __future__ import annotations

from collections import Counter

from sqlalchemy import select

from app.models import ExceptionCase, Order
from app.seed import reset_demo_data


def test_compact_scale_is_default_and_covers_every_exception_type(db_session, bootstrap):
    summary = reset_demo_data(db_session, workspace_id=bootstrap["workspace_id"], with_knowledge=False)
    db_session.commit()

    assert summary["seed_scale"] == "compact"
    counts = summary["counts"]
    assert counts["orders"] == 3, f"精简规模只应有 3 张订单，实际 {counts['orders']}"
    # CASE-D1 送达未违约（25min ≤ 允许 30min）→ 不建单，所以是 2 张异常而不是 3
    assert counts["exceptions"] == 2

    cases = db_session.scalars(select(ExceptionCase)).all()
    per_type = Counter(case.type for case in cases)
    assert set(per_type) == {"VEHICLE_BREAKDOWN", "DELAY_RISK"}, f"应覆盖全部异常类型：{per_type}"
    assert per_type["VEHICLE_BREAKDOWN"] == 1, "车辆故障保留 CASE-A 一单（全闭环展示）"
    assert per_type["DELAY_RISK"] == 1, "延误单只有送达超时的 CASE-D2"
    # 延误单只由「预计到达时间」触发（口径 2026-10-08；旧的"送达结算建单"已下线）
    delay_case = next(case for case in cases if case.type == "DELAY_RISK")
    assert delay_case.detection_rule == "DELIVERED_BREACH" and delay_case.sla_breached is True
    assert delay_case.sla_delay_minutes == 31
    # 造数必须把判定时点写到订单上，否则自检口径（延误 = 预计到达 − 承诺送达）不成立
    delay_order = db_session.get(Order, delay_case.order_id)
    assert delay_order is not None and delay_order.planned_delivery_at is not None, "延误单所属订单必须有预计到达时间"
    assert delay_order.promised_delivery_at is not None
    seeded_delay = int(
        round((delay_order.planned_delivery_at - delay_order.promised_delivery_at).total_seconds() / 60)
    )
    assert seeded_delay == delay_case.sla_delay_minutes, (
        f"造数的延误 {seeded_delay} ≠ 单上的 {delay_case.sla_delay_minutes}"
    )
    # 车辆故障单不做 SLA 判定（新模型）
    vehicle_case = next(case for case in cases if case.type == "VEHICLE_BREAKDOWN")
    assert vehicle_case.sla_delay_minutes is None and vehicle_case.sla_breached is False
    # CASE-A 的承运商消息是"消息解析"演示的输入，必须存在
    assert counts["carrier_messages"] == 1
    assert counts["tracking_events"] >= 20

    # 两种规模的订单都能被业务规则算出来（承诺时间非空、SLA 判定有结果）
    for order in db_session.scalars(select(Order)).all():
        assert order.promised_delivery_at is not None
        assert order.dispatched_at is not None


def test_full_scale_still_available(db_session, bootstrap):
    summary = reset_demo_data(
        db_session, workspace_id=bootstrap["workspace_id"], with_knowledge=False, scale="full"
    )
    db_session.commit()
    assert summary["seed_scale"] == "full"
    assert summary["counts"]["orders"] == 1000
    assert summary["counts"]["exceptions"] == 50
    # 完整规模必须覆盖关键等级与四个状态（4 状态模型：旧 CONFIRMING/ANALYZING 已并入处理中）；
    # 新模型下未结束的异常最低 MEDIUM（车辆单 1 分起），LOW 不再出现在异常单的 level 上
    assert set(summary["exceptions_by_level"]) == {"MEDIUM", "HIGH", "CRITICAL"}
    assert set(summary["exceptions_by_status"]) >= {
        "DETECTED",
        "PROCESSING",
        "RESOLVED",
        "CLOSED",
    }


def test_compact_scale_is_idempotent(db_session, bootstrap):
    first = reset_demo_data(db_session, workspace_id=bootstrap["workspace_id"], with_knowledge=False)
    db_session.commit()
    second = reset_demo_data(db_session, workspace_id=bootstrap["workspace_id"], with_knowledge=False)
    db_session.commit()
    assert first["counts"] == second["counts"]
    assert first["case_a"] == second["case_a"]
