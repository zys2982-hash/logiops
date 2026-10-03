"""编号生成契约（Lead 维护）：必须"取最大序号 +1"，不能"行数 +1"。

真实踩坑：seed 预置了 AI20260930000005（当天只有 4 条分析），COUNT+1 也算出 …000005，
于是"重试分析"直接撞 ai_analysis.analysis_no 唯一索引 → 500，用户看到的就是
"重新分析没有用"。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from app.models import AiAnalysis, ExceptionCase, Order
from app.repositories import Repos
from app.services.common import next_analysis_no, next_order_no

DAY = datetime(2026, 9, 30, 1, 0)


def _make_order(session, bootstrap, order_no: str = "SO2026093000016") -> Order:
    order = Order(
        workspace_id=bootstrap["workspace_id"],
        order_no=order_no,
        customer_id=bootstrap["customers"]["vip"].id,
        origin_city="天津",
        dest_city="上海",
        status="CREATED",
    )
    session.add(order)
    session.flush()
    return order


def _make_case(
    session, bootstrap, order: Order, case_no: str = "EX2026093000016", status: str = "DETECTED"
) -> ExceptionCase:
    case = ExceptionCase(
        workspace_id=bootstrap["workspace_id"],
        case_no=case_no,
        order_id=order.id,
        customer_id=bootstrap["customers"]["vip"].id,
        type="VEHICLE_BREAKDOWN",
        status=status,
    )
    session.add(case)
    session.flush()
    return case


def test_analysis_no_uses_max_not_count(db_session, bootstrap):
    """4 行但最大序号是 5 → 下一个必须是 6（而不是 5）。"""
    case = _make_case(db_session, bootstrap, _make_order(db_session, bootstrap))
    for seq in (1, 2, 3, 5):
        db_session.add(
            AiAnalysis(
                workspace_id=bootstrap["workspace_id"],
                exception_id=case.id,
                analysis_no=f"AI2026093000{seq:04d}",
                task_type="ANALYZE_EXCEPTION",
                status="READY",
            )
        )
    db_session.commit()
    repos = Repos(db_session, bootstrap["workspace_id"])
    assert next_analysis_no(repos, moment=DAY) == "AI20260930000006"


def test_analysis_no_starts_at_one_when_empty(db_session, bootstrap):
    repos = Repos(db_session, bootstrap["workspace_id"])
    assert next_analysis_no(repos, moment=DAY) == "AI20260930000001"


def test_order_no_uses_max_not_count(db_session, bootstrap):
    for seq in (1, 999, 1000):  # 3 行但最大 1000
        _make_order(db_session, bootstrap, order_no=f"SO20260930{seq:04d}")
    db_session.commit()
    repos = Repos(db_session, bootstrap["workspace_id"])
    assert next_order_no(repos, moment=DAY) == "SO202609301001"


def test_case_no_uses_max_not_count(db_session, bootstrap):
    order = _make_order(db_session, bootstrap)
    for seq in (2, 259):  # 2 行但最大 259
        _make_case(db_session, bootstrap, order, case_no=f"EX20260930{seq:04d}")
    db_session.commit()
    repos = Repos(db_session, bootstrap["workspace_id"])
    assert repos.exceptions.next_sequence("EX20260930") == 260


def test_retry_after_failure_produces_unique_analysis_no(client, db_session, bootstrap, admin_headers):
    """失败后重试必须成功且编号唯一（不再 500）。"""
    case = _make_case(db_session, bootstrap, _make_order(db_session, bootstrap), status="CONFIRMING")
    db_session.add(
        AiAnalysis(
            workspace_id=bootstrap["workspace_id"],
            exception_id=case.id,
            analysis_no="AI20260930000005",  # 复现"最大编号 5、行数 1"的撞号场景
            task_type="ANALYZE_EXCEPTION",
            status="FAILED",
            error_code="AI_OUTPUT_INVALID",
        )
    )
    db_session.commit()
    failed = db_session.scalars(select(AiAnalysis).order_by(AiAnalysis.id.desc())).first()

    response = client.post(f"/api/v1/ai-analyses/{failed.id}/retry", headers=admin_headers)
    assert response.status_code in (200, 202), response.text

    numbers = [row.analysis_no for row in db_session.scalars(select(AiAnalysis)).all()]
    assert len(numbers) == len(set(numbers)), f"编号必须唯一，实际 {sorted(numbers)}"
    assert "AI20260930000006" in numbers, f"应使用最大序号 +1，实际 {sorted(numbers)}"
