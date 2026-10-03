"""ETA 重算默认停用（docs/08：不再按车速/里程模拟到达时间）。

运行时 `eta_enabled=False`（config 默认）→ `eta_flow.recalc_order_eta` 在唯一入口提前返回，
沿用订单既有 ETA 快照，**不改写订单**。调用方（tick / 写轨迹 / 消息解析 / 送达）因此全部失效为 no-op。
测试环境由 conftest 置 `ETA_ENABLED=true`，ETA 引擎自身的测试继续覆盖（代码保留以便回溯）。
"""

from __future__ import annotations

from app.core.config import get_settings
from app.repositories import Repos
from app.services import eta_flow


def _first_order(db_session, bootstrap):
    from app.models import Order

    order = db_session.query(Order).filter(Order.workspace_id == bootstrap["workspace_id"]).first()
    assert order is not None, "seed 未生效：没有订单"
    return order


def test_recalc_is_disabled_by_default_and_keeps_existing_eta(db_session, bootstrap):
    """停用时：返回既有 ETA 快照，且**订单的 current_eta_at 不变**（不再做速度模拟）。"""
    from app.seed import reset_demo_data

    reset_demo_data(db_session, workspace_id=bootstrap["workspace_id"], with_knowledge=False)
    db_session.commit()

    order = _first_order(db_session, bootstrap)
    before = order.current_eta_at
    settings = get_settings()
    original = settings.eta_enabled
    settings.eta_enabled = False
    try:
        result = eta_flow.recalc_order_eta(Repos(db_session, bootstrap["workspace_id"]), order)
        db_session.commit()
        db_session.refresh(order)
    finally:
        settings.eta_enabled = original

    assert order.current_eta_at == before, "ETA 重算停用后不得改写订单 ETA"
    assert result.eta_at is not None, "应返回一个 ETA 值（既有快照），而不是抛异常"
    assert "停用" in (result.detail or ""), f"应说明已停用，实际 detail={result.detail!r}"


def test_recalc_still_works_when_enabled(db_session, bootstrap):
    """开关打开时引擎照常工作（保证保留的代码没有腐烂，便于将来恢复/重建）。"""
    from app.seed import reset_demo_data

    reset_demo_data(db_session, workspace_id=bootstrap["workspace_id"], with_knowledge=False)
    db_session.commit()

    order = _first_order(db_session, bootstrap)
    settings = get_settings()
    original = settings.eta_enabled
    settings.eta_enabled = True
    try:
        result = eta_flow.recalc_order_eta(Repos(db_session, bootstrap["workspace_id"]), order)
    finally:
        settings.eta_enabled = original

    assert result.eta_at is not None
    assert "停用" not in (result.detail or "")
