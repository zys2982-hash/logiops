"""演示数据状态不变量 + ANALYZING 遗留状态自愈（Lead 维护）。

背景：seed 曾把 ANALYZING 当常态铺数据，导致"显示分析中、没有分析记录、又不能发起分析"
的死胡同（用户实际点到了 4 条这样的异常）。ANALYZING 只应存在于"确有分析任务在跑"时。
"""

from __future__ import annotations

from sqlalchemy import select

from app.models import AiAnalysis, ExceptionCase, Order


def _make_order(session, bootstrap, order_no: str) -> Order:
    order = Order(
        workspace_id=bootstrap["workspace_id"],
        order_no=order_no,
        customer_id=bootstrap["customers"]["vip"].id,
        origin_city="天津",
        dest_city="上海",
        status="IN_TRANSIT",
    )
    session.add(order)
    session.flush()
    return order


def _make_case(session, bootstrap, order: Order, case_no: str, status: str) -> ExceptionCase:
    case = ExceptionCase(
        workspace_id=bootstrap["workspace_id"],
        case_no=case_no,
        order_id=order.id,
        customer_id=bootstrap["customers"]["vip"].id,
        type="DELAY_RISK",
        status=status,
    )
    session.add(case)
    session.flush()
    return case


def test_seed_has_no_analyzing_case_without_running_analysis(db_session, bootstrap):
    """演示数据里不允许出现"没有在跑任务却停在 ANALYZING"的异常。"""
    from app.seed import reset_demo_data

    reset_demo_data(db_session, workspace_id=bootstrap["workspace_id"], with_knowledge=False)
    db_session.commit()
    analyzing = db_session.scalars(
        select(ExceptionCase).where(ExceptionCase.status == "ANALYZING")
    ).all()
    for case in analyzing:
        running = db_session.scalars(
            select(AiAnalysis).where(
                AiAnalysis.exception_id == case.id,
                AiAnalysis.status.in_(["PENDING", "RUNNING"]),
            )
        ).first()
        assert running is not None, (
            f"{case.case_no} 停在 ANALYZING 却没有在跑的分析任务（这正是用户点不到 AI 分析的原因）"
        )


def test_analyze_recovers_stale_analyzing_state(client, db_session, bootstrap, admin_headers):
    """遗留的 ANALYZING（无在跑任务）→ 发起分析时自动回退 CONFIRMING 并继续，不再是死胡同。"""
    order = _make_order(db_session, bootstrap, "SO202609300900")
    case = _make_case(db_session, bootstrap, order, "EX202609300900", status="ANALYZING")
    db_session.commit()
    version = case.version

    response = client.post(
        f"/api/v1/exceptions/{case.id}/analyze",
        headers=admin_headers,
        json={"expected_version": version},
    )
    assert response.status_code in (200, 202), response.text

    refreshed = client.get(f"/api/v1/exceptions/{case.id}", headers=admin_headers).json()
    assert refreshed["status"] in {"ANALYZING", "PROCESSING"}, refreshed["status"]

    analyses = db_session.scalars(
        select(AiAnalysis).where(AiAnalysis.exception_id == case.id)
    ).all()
    assert analyses, "自愈后必须真的创建了分析任务"


def test_analyze_still_rejects_when_analysis_running(client, db_session, bootstrap, admin_headers):
    """确有任务在跑时，仍然拒绝重复发起（409 AI_ANALYSIS_IN_PROGRESS）。"""
    order = _make_order(db_session, bootstrap, "SO202609300901")
    case = _make_case(db_session, bootstrap, order, "EX202609300901", status="ANALYZING")
    db_session.add(
        AiAnalysis(
            workspace_id=bootstrap["workspace_id"],
            exception_id=case.id,
            analysis_no="AI20260930009999",
            task_type="ANALYZE_EXCEPTION",
            status="RUNNING",
        )
    )
    db_session.commit()

    response = client.post(
        f"/api/v1/exceptions/{case.id}/analyze",
        headers=admin_headers,
        json={"expected_version": case.version},
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "AI_ANALYSIS_IN_PROGRESS"
