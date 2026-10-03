"""Dashboard 统计 + Seed 固定数据测试（基线文档 §10.3【Dashboard】、§13.2、§13.3、S6）。

断言口径：**等级/违约/ETA 都是规则算出来的**，不是 seed 硬编码的。
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from app.core.clock import parse_dt, utcnow_naive
from app.models.exception import CarrierMessage, ExceptionCase
from app.models.master import Customer, Driver, SlaRule, Vehicle
from app.models.transport import Order, TrackingEvent
from app.rules import detection
from app.seed import reset_demo_data

OPERATOR = "operator@logiops.dev"
CASE_A_ORDER_NO = "SO20260930021"


def seed_workspace(db_session, bootstrap, **kwargs):
    # 本文件断言的是**完整规模**（1000 单 / 50 异常），显式指定 scale="full"：
    # 演示默认规模已改为 compact（每种异常类型一单，见 tests/test_seed_compact.py）
    kwargs.setdefault("scale", "full")
    summary = reset_demo_data(
        db_session,
        workspace_id=bootstrap["workspace_id"],
        with_knowledge=False,
        **kwargs,
    )
    db_session.commit()
    return summary


def test_seed_scale_and_idempotency(client, db_session, bootstrap):
    first = seed_workspace(db_session, bootstrap)
    counts = first["counts"]
    assert counts["users"] == 4
    assert counts["customers"] == 12
    assert counts["carriers"] == 4
    assert counts["vehicles"] == 24
    assert counts["drivers"] == 24
    assert counts["sla_rules"] == 3
    assert counts["orders"] == 1000
    assert counts["tracking_events"] >= 5000
    assert counts["exceptions"] == 50
    assert first["orders_by_status"] == {"CREATED": 300, "DELIVERED": 300, "DISPATCHED": 100, "IN_TRANSIT": 300}
    assert set(first["exceptions_by_level"]) == {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
    # 4 状态模型：演示数据只铺 待确认/处理中/已解决/已关闭
    # （旧的 CONFIRMING / ANALYZING 已并入 PROCESSING，不再由任何流程产生）
    assert set(first["exceptions_by_status"]) >= {"DETECTED", "PROCESSING", "RESOLVED", "CLOSED"}
    assert "CONFIRMING" not in first["exceptions_by_status"]
    assert "ANALYZING" not in first["exceptions_by_status"]

    second = seed_workspace(db_session, bootstrap)
    assert second["counts"] == counts
    assert second["exceptions_by_level"] == first["exceptions_by_level"]
    assert second["exceptions_by_status"] == first["exceptions_by_status"]
    assert second["case_a"] == first["case_a"]


def test_case_a_is_computed_by_rules(client, db_session, bootstrap):
    summary = seed_workspace(db_session, bootstrap)
    case_a = summary["case_a"]
    assert case_a["case_no"] == "EX20260930001"
    assert case_a["order_no"] == CASE_A_ORDER_NO
    assert case_a["detection_rule"] == "STALL_OVER_THRESHOLD"
    assert 240 <= case_a["sla_delay_minutes"] <= 300
    assert case_a["sla_breached"] is True
    assert case_a["level"] == "CRITICAL"
    assert case_a["risk_score"] == 4
    assert case_a["eta_method"] in {"REPAIR_WAIT", "MOVING_AVG_SPEED", "FALLBACK"}
    assert "VIP-01" in case_a["sla_rule"]

    order = db_session.scalars(select(Order).where(Order.order_no == CASE_A_ORDER_NO)).one()
    case = db_session.scalars(select(ExceptionCase).where(ExceptionCase.order_id == order.id)).one()
    assert case.detection_rule == "STALL_OVER_THRESHOLD"
    assert case.type == "VEHICLE_BREAKDOWN"
    assert case.status in {"DETECTED", "PROCESSING"}
    assert case.promised_delivery_at + timedelta(minutes=270) == case.expected_eta_at
    assert case.risk_factors_json and {item["code"] for item in case.risk_factors_json} >= {
        "DELAY_BASE",
        "CUSTOMER_VIP",
        "VEHICLE_BREAKDOWN",
        "SLA_BREACH",
    }

    # 车辆状态 REPAIRING / 位置 济南
    vehicle = db_session.get(Vehicle, order.vehicle_id)
    assert vehicle is not None and vehicle.status == "REPAIRING" and vehicle.current_city == "济南"
    assert vehicle.plate_no == "津A·12345"

    # 最后一条"移动类"轨迹距业务基准时间 180 分钟 → 检测规则真的会命中
    events = list(
        db_session.scalars(
            select(TrackingEvent).where(TrackingEvent.order_id == order.id).order_by(TrackingEvent.occurred_at)
        )
    )
    moving = [item for item in events if item.event_type not in {"STOP", "NOTE"}]
    assert moving, "CASE-A 必须有移动类轨迹"
    now = utcnow_naive()
    stall_minutes = int((now - moving[-1].occurred_at).total_seconds() // 60)
    assert stall_minutes == 180
    decision = detection.decide(
        now=now,
        order_status=order.status,
        last_move_at=moving[-1].occurred_at,
        stall_threshold_minutes=120,
    )
    assert decision.rule == "STALL_OVER_THRESHOLD" and decision.should_create is True

    # 承运商消息原文 + 解析结果（20:00+08）
    message = db_session.scalars(
        select(CarrierMessage).where(CarrierMessage.exception_id == case.id)
    ).one()
    assert message.channel == "MANUAL_PASTE"
    assert message.raw_text == "车在济南爆胎了，现在联系修理厂，预计晚上 8 点恢复。"
    recovery = parse_dt(message.parse_result_json["estimated_recovery_at"])
    assert recovery == parse_dt("2026-09-30T20:00:00+08:00")
    assert message.parse_result_json["status"] == "REPAIRING"


def test_case_d_boundary_is_not_breached(client, db_session, bootstrap):
    seed_workspace(db_session, bootstrap)
    order = db_session.scalars(select(Order).where(Order.order_no == "SO20260930024")).one()
    case_d = db_session.scalars(select(ExceptionCase).where(ExceptionCase.order_id == order.id)).one()
    assert case_d.sla_delay_minutes == 25
    assert case_d.sla_breached is False
    assert case_d.level == "MEDIUM"
    assert case_d.detection_rule == "MANUAL"

    cases = list(db_session.scalars(select(ExceptionCase).order_by(ExceptionCase.id)))
    breached = [case for case in cases if case.sla_delay_minutes == 31]
    assert breached and all(case.sla_breached is True for case in breached)

    invalid = [case for case in cases if case.close_reason == "INVALID"]
    assert invalid and invalid[0].status == "CLOSED"


def test_case_b_closed_case_has_full_history(client, db_session, bootstrap):
    seed_workspace(db_session, bootstrap)
    from app.models.ai import AiAnalysis, Approval
    from app.models.exception import ExceptionEvent, FollowupTask, Notification

    cases = list(db_session.scalars(select(ExceptionCase).where(ExceptionCase.close_reason == "DELIVERED")))
    assert len(cases) >= 1
    closed = cases[0]
    assert closed.status == "CLOSED" and closed.closed_at is not None
    assert db_session.scalars(select(AiAnalysis).where(AiAnalysis.exception_id == closed.id)).first() is not None
    assert db_session.scalars(select(Approval).where(Approval.exception_id == closed.id)).first() is not None
    assert db_session.scalars(select(Notification).where(Notification.exception_id == closed.id)).first() is not None
    assert db_session.scalars(select(FollowupTask).where(FollowupTask.exception_id == closed.id)).first() is not None
    assert len(db_session.scalars(select(ExceptionEvent).where(ExceptionEvent.exception_id == closed.id)).all()) >= 5


def test_seed_master_data_snapshot(client, db_session, bootstrap):
    seed_workspace(db_session, bootstrap)
    workspace_id = bootstrap["workspace_id"]
    customers = list(db_session.scalars(select(Customer).where(Customer.workspace_id == workspace_id)))
    assert {item.code for item in customers} >= {"VIP-01", "NORM-01", "SVIP-01"}
    assert sum(1 for item in customers if item.level == "VIP") == 3
    assert sum(1 for item in customers if item.level == "SVIP") == 1
    assert next(item for item in customers if item.code == "VIP-01").name == "远洋集团"
    assert next(item for item in customers if item.code == "NORM-01").name == "华北贸易"

    rules = list(db_session.scalars(select(SlaRule).where(SlaRule.workspace_id == workspace_id)))
    default = next(item for item in rules if item.scope_type == "DEFAULT")
    vip = next(item for item in rules if item.scope_type == "CUSTOMER_LEVEL")
    special = next(item for item in rules if item.scope_type == "CUSTOMER")
    assert (default.deadline_offset_hours, default.max_delay_minutes) == (30, 30)
    assert (vip.deadline_offset_hours, vip.max_delay_minutes) == (24, 0)
    assert special.scope_value == "VIP-01" and (special.deadline_offset_hours, special.max_delay_minutes) == (24, 0)

    drivers = list(db_session.scalars(select(Driver).where(Driver.workspace_id == workspace_id)))
    vehicles = list(db_session.scalars(select(Vehicle).where(Vehicle.workspace_id == workspace_id)))
    assert len(drivers) == 24 and len(vehicles) == 24
    assert all(vehicle.current_driver_id is not None for vehicle in vehicles)


def test_seed_reset_is_foreign_key_safe(bootstrap):
    """SQLite 默认不校验外键，只有 MySQL 才会暴露"清空顺序"错误（实测：approval 必须先于 ai_analysis）。

    这里在开启 ``PRAGMA foreign_keys=ON`` 的独立连接上跑两次 reset，把这类问题挡在 CI 里。
    """
    import os

    import pytest
    from sqlalchemy import create_engine, event, text
    from sqlalchemy.orm import sessionmaker

    url = os.environ.get("TEST_DATABASE_URL", "")
    if not url.startswith("sqlite"):
        pytest.skip("该回归用例仅在 SQLite 下补跑外键校验；MySQL 由真库 seed 直接验证")

    engine = create_engine(url, future=True, connect_args={"check_same_thread": False})
    event.listen(engine, "connect", lambda conn, _: conn.execute("PRAGMA foreign_keys=ON"))
    session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    try:
        assert session.execute(text("PRAGMA foreign_keys")).scalar() == 1
        first = reset_demo_data(session, workspace_id=bootstrap["workspace_id"], with_knowledge=False)
        session.commit()
        second = reset_demo_data(session, workspace_id=bootstrap["workspace_id"], with_knowledge=False)
        session.commit()
        assert second["counts"] == first["counts"]
        assert second["exceptions_by_level"] == first["exceptions_by_level"]
    finally:
        session.close()
        engine.dispose()


def test_seed_writes_demo_clock_settings(client, db_session, bootstrap):
    """seed 必须把演示基准/偏移写进 system_setting，并提供 tick 侧的同步入口。"""
    from app.core.clock import state as clock_state
    from app.models.auth import SystemSetting
    from app.seed import prepare_demo_clock, sync_demo_clock_setting

    summary = seed_workspace(db_session, bootstrap)
    assert summary["clock"]["offset_minutes"] == 0
    rows = {row.setting_key: row.setting_value for row in db_session.scalars(select(SystemSetting))}
    assert rows["demo.base_date"] == "2026-09-30T09:00:00+08:00"
    assert rows["demo.clock_offset_minutes"] == "0"
    assert rows["demo.scenario"] == "case-a"

    # tick 侧同步入口：推进时钟后回写偏移（demo.py::demo_tick 调一行即可）
    prepare_demo_clock()
    try:
        clock_state.advance(90)
        offset = sync_demo_clock_setting(db_session, bootstrap["workspace_id"])
        db_session.commit()
        assert offset == 90
        refreshed = db_session.scalars(
            select(SystemSetting).where(SystemSetting.setting_key == "demo.clock_offset_minutes")
        ).one()
        assert refreshed.setting_value == "90"
    finally:
        clock_state.reset()


def test_dashboard_summary(client, db_session, bootstrap):
    seed_workspace(db_session, bootstrap)
    headers = bootstrap["headers"]["OPERATOR"]
    response = client.get("/api/v1/dashboard/summary", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["today_orders"] > 0
    assert body["in_transit"] == 400
    assert body["exceptions_total"] == 50
    assert body["open_exceptions"] + body["closed"] == 50
    assert body["high_risk"] > 0
    assert body["sla_breached"] > 0
    assert body["pending"] >= 1 and body["processing"] >= 1 and body["resolved"] >= 1
    assert body["by_level"]["CRITICAL"] >= 1
    assert sum(body["by_level"].values()) == 50
    assert sum(body["by_status"].values()) == 50
    assert 1 <= len(body["high_risk_top"]) <= 5
    scores = [item["risk_score"] for item in body["high_risk_top"]]
    assert scores == sorted(scores, reverse=True)
    assert all(item["level"] in {"HIGH", "CRITICAL"} for item in body["high_risk_top"])
    assert len(body["trend"]) == 7
    assert body["business_date"] == "2026-09-30"
    assert body["now_utc"].endswith("Z")


def test_dashboard_trend_endpoint(client, db_session, bootstrap):
    seed_workspace(db_session, bootstrap)
    headers = bootstrap["headers"]["VIEWER"]
    response = client.get("/api/v1/dashboard/trend?days=7", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["days"] == 7
    assert len(body["items"]) == 7 == len(body["trend"])
    assert body["start_date"] < body["end_date"]
    dates = [item["date"] for item in body["items"]]
    assert dates == sorted(dates)
    assert sum(item["detected"] for item in body["items"]) == 50
    assert all(set(item) >= {"date", "detected", "breached", "resolved", "closed"} for item in body["items"])

    too_large = client.get("/api/v1/dashboard/trend?days=99", headers=headers)
    assert too_large.status_code == 422


def test_dashboard_requires_auth(client, bootstrap):
    assert client.get("/api/v1/dashboard/summary").status_code == 401
