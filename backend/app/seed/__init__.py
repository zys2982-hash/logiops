"""固定 Seed 数据生成器（基线文档 §13.2 / §13.3）。

对外契约（其他模块/AI/demo 只依赖这两个函数）::

    reset_demo_data(session, *, workspace_id=None, scenario="case-a") -> dict  # 先清空该工作区业务数据再重建（幂等）
    seed_all(session, *, workspace_id=None) -> dict                            # 只写入，不清空

规模：1 工作区 / 4 用户 / 12 客户 / 4 承运商 / 24 车辆 / 24 司机 / 3 SLA 规则 /
1000 订单 / 5500+ 轨迹 / 50 异常（含 CASE-A..E 脚本化案例）。
随机种子固定 20260930；所有状态、等级、ETA、违约都由 app.rules 真算。
"""

from __future__ import annotations

import inspect
import logging
import random
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.clock import state as clock_state
from app.core.clock import utcnow_naive
from app.core.config import get_settings
from app.core.security import hash_password
from app.models.ai import AiAnalysis, AiAnalysisStep, Approval
from app.models.auth import User, Workspace, WorkspaceMember
from app.models.exception import CarrierMessage, ExceptionCase, ExceptionEvent, FollowupTask, Notification
from app.models.master import Carrier, Customer, Driver, SlaRule, Vehicle
from app.models.ops import AuditLog
from app.models.transport import Order, TrackingEvent
from app.repositories import Repos
from app.seed import catalog
from app.seed.cases import add_audit, build_all_cases
from app.seed.orders import ORDER_TOTAL, build_orders, build_tracking_events
from app.seed.state import SeedContext

logger = logging.getLogger("logiops.seed")

DEFAULT_SCENARIO = "case-a"

__all__ = ["prepare_demo_clock", "reset_demo_data", "seed_all", "sync_demo_clock_setting"]


# --- 清空（幂等的前提） -------------------------------------------------------
def _clear_workspace(session: Session, workspace_id: int) -> dict[str, int]:
    """按 FK 依赖顺序清空该工作区的业务数据（外键 RESTRICT，必须从子表开始）。

    注意 approval 同时引用 ai_analysis 与 exception_case，所以必须在 ai_analysis 之前删；
    SQLite 默认不校验外键，顺序错了只有 MySQL 才会报 1451（tests/api/test_dashboard.py 有 FK 开启的回归用例）。
    """
    analysis_ids = select(AiAnalysis.id).where(AiAnalysis.workspace_id == workspace_id)
    case_ids = select(ExceptionCase.id).where(ExceptionCase.workspace_id == workspace_id)
    deleted: dict[str, int] = {}

    statements = [
        ("ai_analysis_step", delete(AiAnalysisStep).where(AiAnalysisStep.analysis_id.in_(analysis_ids))),
        ("approval", delete(Approval).where(Approval.workspace_id == workspace_id)),
        ("ai_analysis", delete(AiAnalysis).where(AiAnalysis.workspace_id == workspace_id)),
        ("notification", delete(Notification).where(Notification.workspace_id == workspace_id)),
        ("followup_task", delete(FollowupTask).where(FollowupTask.workspace_id == workspace_id)),
        ("carrier_message", delete(CarrierMessage).where(CarrierMessage.workspace_id == workspace_id)),
        ("exception_event", delete(ExceptionEvent).where(ExceptionEvent.exception_id.in_(case_ids))),
        ("exception_case", delete(ExceptionCase).where(ExceptionCase.workspace_id == workspace_id)),
        ("tracking_event", delete(TrackingEvent).where(TrackingEvent.workspace_id == workspace_id)),
        ("order", delete(Order).where(Order.workspace_id == workspace_id)),
        ("sla_rule", delete(SlaRule).where(SlaRule.workspace_id == workspace_id)),
        ("vehicle", delete(Vehicle).where(Vehicle.workspace_id == workspace_id)),
        ("driver", delete(Driver).where(Driver.workspace_id == workspace_id)),
        ("customer", delete(Customer).where(Customer.workspace_id == workspace_id)),
        ("carrier", delete(Carrier).where(Carrier.workspace_id == workspace_id)),
        ("workspace_member", delete(WorkspaceMember).where(WorkspaceMember.workspace_id == workspace_id)),
        ("audit_log", delete(AuditLog).where(AuditLog.workspace_id == workspace_id)),
    ]
    for name, statement in statements:
        result = session.execute(statement)
        deleted[name] = int(result.rowcount or 0)
    session.flush()
    return deleted


# --- 工作区与用户 -------------------------------------------------------------
def _ensure_workspace(session: Session, workspace_id: int | None) -> Workspace:
    if workspace_id is not None:
        workspace = session.get(Workspace, workspace_id)
        if workspace is None:
            raise ValueError(f"工作区不存在：workspace_id={workspace_id}")
        workspace.name = workspace.name or catalog.WORKSPACE_NAME
        workspace.status = "ACTIVE"
        session.add(workspace)
        session.flush()
        return workspace

    workspace = session.scalars(select(Workspace).where(Workspace.code == catalog.WORKSPACE_CODE)).first()
    if workspace is None:
        workspace = Workspace(name=catalog.WORKSPACE_NAME, code=catalog.WORKSPACE_CODE, owner_user_id=0)
        session.add(workspace)
        session.flush()
    workspace.name = catalog.WORKSPACE_NAME
    workspace.status = "ACTIVE"
    session.add(workspace)
    session.flush()
    return workspace


def _upsert_users(ctx: SeedContext) -> None:
    session, workspace = ctx.session, ctx.workspace
    password_hash = hash_password(catalog.DEMO_PASSWORD)
    for spec in catalog.SEED_USERS:
        user = session.scalars(select(User).where(User.email == spec["email"])).first()
        if user is None:
            user = User(
                email=spec["email"],
                name=spec["name"],
                phone=spec["phone"],
                password_hash=password_hash,
                status="ACTIVE",
            )
            session.add(user)
            session.flush()
        else:
            user.name = spec["name"]
            user.phone = spec["phone"]
            user.password_hash = password_hash
            user.status = "ACTIVE"
            session.add(user)
        ctx.users[spec["role"]] = user
        session.add(
            WorkspaceMember(
                workspace_id=workspace.id,
                user_id=user.id,
                role=spec["role"],
                status="ACTIVE",
                joined_at=ctx.now,
            )
        )
        ctx.bump("workspace_members")
    workspace.owner_user_id = ctx.users["OWNER"].id
    session.add(workspace)
    session.flush()


# --- 主数据 -------------------------------------------------------------------
def _create_master_data(ctx: SeedContext) -> None:
    session = ctx.session
    workspace_id = ctx.workspace_id
    rng = ctx.rng
    now = ctx.now

    for spec in catalog.SEED_CUSTOMERS:
        session.add(
            Customer(
                workspace_id=workspace_id,
                code=spec["code"],
                name=spec["name"],
                level=spec["level"],
                contact_name=spec["contact_name"],
                contact_phone=spec["phone"],
                contact_email=spec["email"],
                notify_pref="MANUAL_COPY",
                status="ACTIVE",
                created_at=now,
                updated_at=now,
            )
        )
        ctx.bump("customers")

    for spec in catalog.SEED_CARRIERS:
        session.add(
            Carrier(
                workspace_id=workspace_id,
                code=spec["code"],
                name=spec["name"],
                contact_name=spec["contact_name"],
                contact_phone=spec["phone"],
                service_level="NORMAL",
                status="ACTIVE",
                created_at=now,
                updated_at=now,
            )
        )
        ctx.bump("carriers")

    session.flush()
    ctx.customers = list(
        session.scalars(select(Customer).where(Customer.workspace_id == workspace_id).order_by(Customer.id))
    )
    ctx.carriers = list(
        session.scalars(select(Carrier).where(Carrier.workspace_id == workspace_id).order_by(Carrier.id))
    )

    for spec in catalog.build_drivers():
        session.add(
            Driver(
                workspace_id=workspace_id,
                name=spec["name"],
                phone=spec["phone"],
                carrier_id=ctx.carriers[spec["carrier_index"]].id,
                license_no=spec["license_no"],
                status=spec["status"],
                created_at=now,
                updated_at=now,
            )
        )
        ctx.bump("drivers")

    for spec in catalog.SEED_SLA_RULES:
        session.add(
            SlaRule(
                workspace_id=workspace_id,
                name=spec["name"],
                scope_type=spec["scope_type"],
                scope_value=spec["scope_value"],
                deadline_offset_hours=spec["deadline_offset_hours"],
                max_delay_minutes=spec["max_delay_minutes"],
                priority=spec["priority"],
                description=spec["description"],
                is_active=True,
                created_at=now,
                updated_at=now,
            )
        )
        ctx.bump("sla_rules")

    session.flush()
    ctx.drivers = list(
        session.scalars(select(Driver).where(Driver.workspace_id == workspace_id).order_by(Driver.id))
    )
    ctx.sla_rules = list(
        session.scalars(select(SlaRule).where(SlaRule.workspace_id == workspace_id).order_by(SlaRule.priority))
    )

    for spec in catalog.build_vehicles():
        session.add(
            Vehicle(
                workspace_id=workspace_id,
                plate_no=spec["plate_no"],
                vehicle_type=spec["vehicle_type"],
                capacity_ton=Decimal(str(spec["capacity_ton"])),
                carrier_id=ctx.carriers[spec["carrier_index"]].id,
                status=spec["status"],
                current_driver_id=ctx.drivers[spec["driver_index"]].id,
                current_city=spec["current_city"],
                remark=spec["remark"],
                created_at=now,
                updated_at=now,
            )
        )
        ctx.bump("vehicles")
    session.flush()
    ctx.vehicles = list(
        session.scalars(select(Vehicle).where(Vehicle.workspace_id == workspace_id).order_by(Vehicle.id))
    )
    ctx.rng = rng


# --- 统计 ---------------------------------------------------------------------
def _count(session: Session, model: Any, workspace_id: int | None = None) -> int:
    statement = select(func.count()).select_from(model)
    if workspace_id is not None and hasattr(model, "workspace_id"):
        statement = statement.where(model.workspace_id == workspace_id)
    return int(session.scalar(statement) or 0)


def _summary(ctx: SeedContext, *, scenario: str, cleared: dict[str, int]) -> dict[str, Any]:
    session, workspace_id = ctx.session, ctx.workspace_id
    by_level = {
        str(row[0]): int(row[1])
        for row in session.execute(
            select(ExceptionCase.level, func.count())
            .where(ExceptionCase.workspace_id == workspace_id)
            .group_by(ExceptionCase.level)
        ).all()
    }
    by_status = {
        str(row[0]): int(row[1])
        for row in session.execute(
            select(ExceptionCase.status, func.count())
            .where(ExceptionCase.workspace_id == workspace_id)
            .group_by(ExceptionCase.status)
        ).all()
    }
    case_a = session.scalars(
        select(ExceptionCase)
        .join(Order, Order.id == ExceptionCase.order_id)
        .where(ExceptionCase.workspace_id == workspace_id, Order.order_no == catalog.CASE_A_ORDER_NO)
    ).first()

    return {
        "scenario": scenario,
        "workspace_id": workspace_id,
        "workspace_code": ctx.workspace.code,
        "business_now_utc": f"{ctx.now.isoformat()}Z",
        "random_seed": catalog.RANDOM_SEED,
        "cleared": cleared,
        "counts": {
            "users": len(ctx.users),
            "workspace_members": _count(session, WorkspaceMember, workspace_id),
            "customers": _count(session, Customer, workspace_id),
            "carriers": _count(session, Carrier, workspace_id),
            "vehicles": _count(session, Vehicle, workspace_id),
            "drivers": _count(session, Driver, workspace_id),
            "sla_rules": _count(session, SlaRule, workspace_id),
            "orders": _count(session, Order, workspace_id),
            "tracking_events": _count(session, TrackingEvent, workspace_id),
            "exceptions": _count(session, ExceptionCase, workspace_id),
            "exception_events": int(
                session.scalar(
                    select(func.count())
                    .select_from(ExceptionEvent)
                    .join(ExceptionCase, ExceptionCase.id == ExceptionEvent.exception_id)
                    .where(ExceptionCase.workspace_id == workspace_id)
                )
                or 0
            ),
            "carrier_messages": _count(session, CarrierMessage, workspace_id),
            "ai_analyses": _count(session, AiAnalysis, workspace_id),
            "approvals": _count(session, Approval, workspace_id),
            "followup_tasks": _count(session, FollowupTask, workspace_id),
            "notifications": _count(session, Notification, workspace_id),
            "audit_logs": _count(session, AuditLog, workspace_id),
        },
        "orders_by_status": {
            str(row[0]): int(row[1])
            for row in session.execute(
                select(Order.status, func.count()).where(Order.workspace_id == workspace_id).group_by(Order.status)
            ).all()
        },
        "exceptions_by_level": by_level,
        "exceptions_by_status": by_status,
        "case_a": (
            {
                "case_no": case_a.case_no,
                "order_no": catalog.CASE_A_ORDER_NO,
                "customer_code": "VIP-01",
                "detection_rule": case_a.detection_rule,
                "level": case_a.level,
                "status": case_a.status,
                "sla_breached": bool(case_a.sla_breached),
                "sla_delay_minutes": case_a.sla_delay_minutes,
                "risk_score": case_a.risk_score,
                "dispatched_at": (
                    case_a.order.dispatched_at.isoformat()
                    if case_a.order and case_a.order.dispatched_at
                    else None
                ),
                "promised_delivery_at": (
                    case_a.promised_delivery_at.isoformat() if case_a.promised_delivery_at else None
                ),
                "expected_eta_at": case_a.expected_eta_at.isoformat() if case_a.expected_eta_at else None,
                "stall_since": case_a.stall_since.isoformat() if case_a.stall_since else None,
                "eta_method": ctx.counts.get("case_a_eta_method"),
                "eta_detail": ctx.counts.get("case_a_eta_detail"),
                "sla_rule": ctx.counts.get("case_a_sla_rule"),
                "risk_factors": case_a.risk_factors_json,
            }
            if case_a
            else None
        ),
    }


# --- 知识库（AI 智能体提供入口，失败不阻塞） ----------------------------------
def _reindex_knowledge(session: Session, workspace_id: int) -> dict[str, Any]:
    try:
        from app.ai.knowledge_index import reindex_all  # type: ignore[import-not-found]

        signature = inspect.signature(reindex_all)
        params = signature.parameters
        accepts_kwargs = any(param.kind is inspect.Parameter.VAR_KEYWORD for param in params.values())
        kwargs: dict[str, Any] = {}
        if accepts_kwargs or "session" in params:
            kwargs["session"] = session
        if accepts_kwargs or "workspace_id" in params:
            kwargs["workspace_id"] = workspace_id
        result = reindex_all(**kwargs)
        session.flush()
        if isinstance(result, int):
            return {"indexed": result}
        if isinstance(result, dict):
            return dict(result)
        return {"indexed": 0}
    except ImportError as exc:
        return {"indexed": 0, "warning": f"app.ai.knowledge_index 未实现：{exc}"}
    except Exception as exc:  # noqa: BLE001 - 知识库不可用不能阻塞 seed
        session.rollback()
        return {"indexed": 0, "warning": f"知识库重建失败：{type(exc).__name__}: {exc}"}


# --- 时钟（演示可复现的关键） -------------------------------------------------
def prepare_demo_clock() -> dict[str, Any]:
    """把业务时钟归零到 ``DEMO_BASE_DATE``（offset=0），并把基准时间交接给库。

    ``python -m app.seed`` 是独立进程：它能重置"自己进程"里的 ``clock.state``，
    但**无法**改写已经在跑的 uvicorn 进程内存中的 ``clock.state``（跨进程限制）。
    因此这里同时把 ``demo.base_date`` / ``demo.clock_offset_minutes`` / ``demo.scenario``
    写入 ``system_setting``：一是让 seed 之后的应用启动能读到同一基准，
    二是让排查时能看出"库里的数据是按哪个业务时间生成的"。
    对已在运行的 uvicorn：请重启，或调用 ``POST /api/v1/demo/actions/reset``
    （它在本进程内先 ``clock_state.reset()`` 再重建 seed，因此进程内自洽）。
    """
    settings = get_settings()
    if settings.clock_mode.lower() == "system":
        return {"clock_mode": settings.clock_mode, "offset_minutes": 0, "base_date": settings.demo_base_date}
    clock_state.reset()
    return {
        "clock_mode": settings.clock_mode,
        "offset_minutes": clock_state.offset_minutes,
        "base_date": settings.demo_base_date,
    }


def _persist_demo_settings(session: Session, workspace_id: int, scenario: str) -> None:
    """把演示基准写进 system_setting（键与 §7.4 T22 一致）。"""
    repos = Repos(session, workspace_id=workspace_id)
    repos.settings.set("demo.base_date", get_settings().demo_base_date)
    repos.settings.set("demo.clock_offset_minutes", str(clock_state.offset_minutes))
    repos.settings.set("demo.scenario", scenario)


def sync_demo_clock_setting(session: Session, workspace_id: int) -> int:
    """把"当前进程"的业务时钟偏移回写 ``system_setting``，返回偏移分钟数。

    ``POST /demo/actions/tick`` 是在 uvicorn 进程内推进 ``clock_state`` 的；调用方
    （demo.py::demo_tick）加一行即可让库里也看到最新偏移：

        from app.seed import sync_demo_clock_setting
        sync_demo_clock_setting(ctx.session, ctx.workspace_id)

    这样前端横幅除了解析 tick 响应，也能从 ``system_setting`` 读到"业务时间/偏移"。
    """
    repos = Repos(session, workspace_id=workspace_id)
    repos.settings.set("demo.base_date", clock_state.anchor().isoformat())
    repos.settings.set("demo.clock_offset_minutes", str(clock_state.offset_minutes))
    return clock_state.offset_minutes


# --- 公开 API -----------------------------------------------------------------
def seed_all(session: Session, *, workspace_id: int | None = None) -> dict[str, Any]:
    """只写入（不清空）：调用方负责保证工作区干净。"""
    clock_info = prepare_demo_clock()
    rng = random.Random(catalog.RANDOM_SEED)
    now = utcnow_naive()
    workspace = _ensure_workspace(session, workspace_id)
    ctx = SeedContext(session=session, workspace=workspace, now=now, rng=rng)

    _upsert_users(ctx)
    _create_master_data(ctx)

    orders, plans = build_orders(
        workspace_id=workspace.id,
        now=now,
        rng=rng,
        customers=ctx.customers,
        carriers=ctx.carriers,
        vehicles=ctx.vehicles,
        drivers=ctx.drivers,
        sla_rules_list=ctx.sla_rules,
    )
    ctx.orders = orders
    ctx.plans = plans
    session.add_all(orders)
    session.flush()
    ctx.bump("orders", len(orders))

    # 先跑案例（会修正 CASE-A 的订单事实与轨迹计划），再落轨迹：保证时间线自洽
    build_all_cases(ctx)

    events = build_tracking_events(workspace_id=workspace.id, orders=orders, plans=plans)
    session.add_all(events)
    ctx.bump("tracking_events", len(events))

    for spec in catalog.SEED_USERS:
        add_audit(
            ctx,
            "auth.login",
            resource_type="user",
            resource_id=ctx.users[spec["role"]].id,
            actor_type="USER",
            actor_id=ctx.users[spec["role"]].id,
            after={"email": spec["email"], "role": spec["role"]},
            source="MANUAL",
        )
    session.flush()

    summary = _summary(ctx, scenario=DEFAULT_SCENARIO, cleared={})
    summary["seed_total_orders"] = ORDER_TOTAL
    summary["clock"] = clock_info
    _persist_demo_settings(session, workspace.id, DEFAULT_SCENARIO)
    logger.info("seed 完成：%s", summary["counts"])
    return summary


def reset_demo_data(
    session: Session,
    *,
    workspace_id: int | None = None,
    scenario: str = DEFAULT_SCENARIO,
    with_knowledge: bool = True,
) -> dict[str, Any]:
    """幂等重建：先清空该工作区的业务数据，再写入固定 seed（同样输入必然同样结果）。"""
    prepare_demo_clock()
    workspace = _ensure_workspace(session, workspace_id)
    cleared = _clear_workspace(session, workspace.id)
    summary = seed_all(session, workspace_id=workspace.id)
    summary["scenario"] = scenario
    summary["cleared"] = cleared
    _persist_demo_settings(session, workspace.id, scenario)
    if with_knowledge:
        knowledge = _reindex_knowledge(session, workspace.id)
        summary["knowledge"] = knowledge
        if knowledge.get("warning"):
            logger.warning("知识库索引未构建：%s", knowledge["warning"])
    session.flush()
    logger.info(
        "reset_demo_data 完成（scenario=%s）：%s",
        scenario,
        summary["counts"],
    )
    return summary
