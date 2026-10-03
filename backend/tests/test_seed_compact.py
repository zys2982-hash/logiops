"""演示数据规模：compact（默认，每种异常类型一单）与 full（1000 单）契约（Lead 维护）。

用户反馈"订单数太乱太多"，默认规模改为 compact：
  3 订单 = CASE-A（车辆故障全闭环）+ CASE-D1/D2（延误风险的 25min 不违约 / 31min 违约边界）
  3 异常 = 覆盖全部 2 种异常类型
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
    assert counts["exceptions"] == 3

    cases = db_session.scalars(select(ExceptionCase)).all()
    per_type = Counter(case.type for case in cases)
    assert set(per_type) == {"VEHICLE_BREAKDOWN", "DELAY_RISK"}, f"应覆盖全部异常类型：{per_type}"
    assert per_type["VEHICLE_BREAKDOWN"] == 1, "车辆故障保留 CASE-A 一单（全闭环展示）"
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
    # 完整规模必须覆盖全部四种等级与四个状态（4 状态模型：旧 CONFIRMING/ANALYZING 已并入处理中）
    assert set(summary["exceptions_by_level"]) == {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
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
