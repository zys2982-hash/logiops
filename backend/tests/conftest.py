"""共享测试脚手架（Lead 维护，所有测试智能体消费）。

策略：
- 默认用 SQLite（临时文件）跑测试，无需 MySQL；设置 TEST_DATABASE_URL 即切 MySQL（CI 用）。
- 每个用例重建全部表，保证隔离与可复现。
- 业务时钟默认 replay，业务基准时间 = 配置的 DEMO_BASE_DATE。
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path

TEST_DB_PATH = Path(tempfile.gettempdir()) / f"logiops_test_{os.getpid()}.sqlite3"
os.environ.setdefault("TEST_DATABASE_URL", f"sqlite+pysqlite:///{TEST_DB_PATH.as_posix()}")
# 应用自身也要指向测试库（TestClient 走 app.db.session.get_db）
os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]
os.environ.setdefault("CLOCK_MODE", "replay")
os.environ.setdefault("AI_MODE", "replay")
os.environ.setdefault("SECRET_KEY", "test-secret-key")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from app.core.clock import parse_dt  # noqa: E402
from app.core.clock import state as clock_state  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.security import create_access_token, hash_password  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Base  # noqa: E402
from app.models.auth import User, Workspace, WorkspaceMember  # noqa: E402
from app.models.master import Carrier, Customer, Driver, SlaRule, Vehicle  # noqa: E402

DEMO_PASSWORD = "Demo@12345"
ROLE_EMAILS = {
    "OWNER": "owner@logiops.dev",
    "ADMIN": "admin@logiops.dev",
    "OPERATOR": "operator@logiops.dev",
    "VIEWER": "viewer@logiops.dev",
}
ROLE_NAMES = {"OWNER": "王总", "ADMIN": "李管理", "OPERATOR": "张三", "VIEWER": "访客"}


@pytest.fixture(scope="session")
def engine():
    url = os.environ["TEST_DATABASE_URL"]
    kwargs: dict = {"future": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    eng = create_engine(url, **kwargs)
    yield eng
    eng.dispose()


def pytest_sessionfinish(session, exitstatus) -> None:  # noqa: ARG001
    """收尾清理：本进程的 SQLite 测试库用完即删（避免 backend/ 堆满 .db 文件）。"""
    if os.environ["TEST_DATABASE_URL"].startswith("sqlite") and TEST_DB_PATH.exists():
        try:
            TEST_DB_PATH.unlink()
        except OSError:  # pragma: no cover - Windows 偶发占用
            pass


@pytest.fixture()
def db_session(engine) -> Iterator[Session]:
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    clock_state.reset()
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = factory()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client(db_session: Session) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def base_time() -> datetime:
    """业务基准时间（replay 时钟的起点）。"""
    clock_state.reset()
    return parse_dt(get_settings().demo_base_date)


@pytest.fixture()
def bootstrap(db_session: Session, base_time: datetime) -> dict:
    """最小可用的演示底座：1 工作区 + 4 角色 + 客户/承运商/车辆/司机 + 3 条 SLA 规则。"""
    workspace = Workspace(name="顺捷物流", code="SJ", owner_user_id=0)
    db_session.add(workspace)
    db_session.flush()

    users: dict[str, User] = {}
    for role, email in ROLE_EMAILS.items():
        user = User(email=email, name=ROLE_NAMES[role], password_hash=hash_password(DEMO_PASSWORD))
        db_session.add(user)
        db_session.flush()
        db_session.add(
            WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role=role, status="ACTIVE")
        )
        users[role] = user
    workspace.owner_user_id = users["OWNER"].id

    customer_vip = Customer(
        workspace_id=workspace.id,
        code="VIP-01",
        name="远洋集团",
        level="VIP",
        contact_name="刘经理",
        contact_phone="13800000001",
        contact_email="liu@yuanyang.example.com",
    )
    customer_normal = Customer(
        workspace_id=workspace.id,
        code="NORM-01",
        name="华北贸易",
        level="NORMAL",
        contact_name="陈经理",
        contact_phone="13800000002",
    )
    db_session.add_all([customer_vip, customer_normal])
    db_session.flush()

    carrier = Carrier(
        workspace_id=workspace.id,
        code="CR-01",
        name="顺达运输",
        contact_name="赵队长",
        contact_phone="13900000001",
    )
    db_session.add(carrier)
    db_session.flush()

    driver = Driver(
        workspace_id=workspace.id,
        name="李四",
        phone="13700000001",
        carrier_id=carrier.id,
        license_no="A1234567",
    )
    vehicle = Vehicle(
        workspace_id=workspace.id,
        plate_no="津A·12345",
        vehicle_type="9.6米厢车",
        capacity_ton=18,
        carrier_id=carrier.id,
        status="IN_TRANSIT",
        current_city="济南",
    )
    db_session.add_all([driver, vehicle])
    db_session.flush()
    vehicle.current_driver_id = driver.id

    sla_rules = [
        SlaRule(
            workspace_id=workspace.id,
            name="默认规则",
            scope_type="DEFAULT",
            scope_value=None,
            deadline_offset_hours=30,
            max_delay_minutes=30,
            priority=100,
        ),
        SlaRule(
            workspace_id=workspace.id,
            name="VIP 客户规则",
            scope_type="CUSTOMER_LEVEL",
            scope_value="VIP",
            deadline_offset_hours=24,
            max_delay_minutes=0,
            priority=10,
        ),
        SlaRule(
            workspace_id=workspace.id,
            name="VIP-01 专属规则",
            scope_type="CUSTOMER",
            scope_value="VIP-01",
            deadline_offset_hours=24,
            max_delay_minutes=0,
            priority=1,
        ),
    ]
    db_session.add_all(sla_rules)
    db_session.commit()

    headers = {}
    for role, user in users.items():
        token, _ = create_access_token(user.id, user.email)
        headers[role] = {"Authorization": f"Bearer {token}", "X-Workspace-Id": str(workspace.id)}

    return {
        "workspace": workspace,
        "workspace_id": workspace.id,
        "users": users,
        "customers": {"vip": customer_vip, "normal": customer_normal},
        "carrier": carrier,
        "driver": driver,
        "vehicle": vehicle,
        "sla_rules": sla_rules,
        "headers": headers,
        "password": DEMO_PASSWORD,
        "base_time": base_time,
        "promised_vip": base_time + timedelta(hours=24),
    }


@pytest.fixture()
def operator_headers(bootstrap: dict) -> dict:
    return bootstrap["headers"]["OPERATOR"]


@pytest.fixture()
def admin_headers(bootstrap: dict) -> dict:
    return bootstrap["headers"]["ADMIN"]


@pytest.fixture()
def viewer_headers(bootstrap: dict) -> dict:
    return bootstrap["headers"]["VIEWER"]


@pytest.fixture()
def owner_headers(bootstrap: dict) -> dict:
    return bootstrap["headers"]["OWNER"]
