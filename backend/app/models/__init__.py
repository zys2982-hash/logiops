"""所有 ORM 模型的唯一出口（Alembic autogenerate 依赖这里）。"""

from __future__ import annotations

from app.db.base import Base
from app.models.ai import AiAnalysis, AiAnalysisStep, Approval
from app.models.auth import SystemSetting, User, Workspace, WorkspaceMember
from app.models.exception import CarrierMessage, ExceptionCase, ExceptionEvent, FollowupTask, Notification
from app.models.master import Carrier, Customer, Driver, SlaRule, Vehicle
from app.models.ops import AuditLog, KnowledgeChunk, KnowledgeDoc
from app.models.transport import Order, TrackingEvent

__all__ = [
    "AiAnalysis",
    "AiAnalysisStep",
    "Approval",
    "AuditLog",
    "Base",
    "Carrier",
    "CarrierMessage",
    "Customer",
    "Driver",
    "ExceptionCase",
    "ExceptionEvent",
    "FollowupTask",
    "KnowledgeChunk",
    "KnowledgeDoc",
    "Notification",
    "Order",
    "SlaRule",
    "SystemSetting",
    "TrackingEvent",
    "User",
    "Vehicle",
    "Workspace",
    "WorkspaceMember",
]

TABLES = [
    User.__tablename__,
    Workspace.__tablename__,
    WorkspaceMember.__tablename__,
    SystemSetting.__tablename__,
    Customer.__tablename__,
    Carrier.__tablename__,
    Vehicle.__tablename__,
    Driver.__tablename__,
    SlaRule.__tablename__,
    Order.__tablename__,
    TrackingEvent.__tablename__,
    ExceptionCase.__tablename__,
    ExceptionEvent.__tablename__,
    CarrierMessage.__tablename__,
    FollowupTask.__tablename__,
    Notification.__tablename__,
    AiAnalysis.__tablename__,
    AiAnalysisStep.__tablename__,
    Approval.__tablename__,
    AuditLog.__tablename__,
    KnowledgeDoc.__tablename__,
    KnowledgeChunk.__tablename__,
]
