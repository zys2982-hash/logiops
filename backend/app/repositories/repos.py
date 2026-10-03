"""具体仓储：每个模型一个类，只做数据访问，不写业务判断。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import String, cast, func, or_, select, text
from sqlalchemy.orm import Session

from app.core.errors import not_found
from app.models.ai import AiAnalysis, AiAnalysisStep, Approval
from app.models.auth import SystemSetting, User, Workspace, WorkspaceMember
from app.models.enums import OPEN_EXCEPTION_STATUSES
from app.models.exception import CarrierMessage, ExceptionCase, ExceptionEvent, FollowupTask, Notification
from app.models.master import Carrier, Customer, Driver, SlaRule, Vehicle
from app.models.ops import AuditLog, KnowledgeChunk, KnowledgeDoc
from app.models.transport import Order, TrackingEvent
from app.repositories.base import BaseRepository


# --- 认证与工作区 -----------------------------------------------------------
class UserRepository(BaseRepository[User]):
    model = User

    def get_by_email(self, email: str) -> User | None:
        return self.session.scalars(select(User).where(User.email == email)).first()


class WorkspaceRepository(BaseRepository[Workspace]):
    model = Workspace

    def __init__(self, session: Session, workspace_id: int | None = None) -> None:
        super().__init__(session, workspace_id=None)

    def list_for_user(self, user_id: int) -> list[tuple[Workspace, str]]:
        stmt = (
            select(Workspace, WorkspaceMember.role)
            .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
            .where(WorkspaceMember.user_id == user_id, WorkspaceMember.status == "ACTIVE")
            .order_by(Workspace.id)
        )
        return [(row[0], row[1]) for row in self.session.execute(stmt).all()]

    def get_by_code(self, code: str) -> Workspace | None:
        return self.session.scalars(select(Workspace).where(Workspace.code == code)).first()


class WorkspaceMemberRepository(BaseRepository[WorkspaceMember]):
    model = WorkspaceMember

    def get_membership(self, workspace_id: int, user_id: int) -> WorkspaceMember | None:
        stmt = select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == user_id,
            WorkspaceMember.status == "ACTIVE",
        )
        return self.session.scalars(stmt).first()

    def list_members(self, workspace_id: int) -> list[tuple[WorkspaceMember, User]]:
        stmt = (
            select(WorkspaceMember, User)
            .join(User, User.id == WorkspaceMember.user_id)
            .where(WorkspaceMember.workspace_id == workspace_id, WorkspaceMember.status == "ACTIVE")
            .order_by(WorkspaceMember.id)
        )
        return [(row[0], row[1]) for row in self.session.execute(stmt).all()]


class SettingRepository:
    def __init__(self, session: Session, workspace_id: int | None = None) -> None:
        self.session = session
        self.workspace_id = workspace_id

    def get(self, key: str, default: str | None = None) -> str | None:
        row = self.session.get(SystemSetting, key)
        return default if row is None or row.setting_value is None else row.setting_value

    def get_int(self, key: str, default: int = 0) -> int:
        value = self.get(key)
        try:
            return int(value) if value is not None else default
        except ValueError:
            return default

    def set(self, key: str, value: str) -> None:
        row = self.session.get(SystemSetting, key)
        if row is None:
            row = SystemSetting(setting_key=key, setting_value=value)
            self.session.add(row)
        else:
            row.setting_value = value
        self.session.flush()


# --- 主数据 -----------------------------------------------------------------
class CustomerRepository(BaseRepository[Customer]):
    model = Customer

    def get_by_code(self, code: str) -> Customer | None:
        return self.get_by(code=code)

    def search(self, *, q: str | None, level: str | None, page: int, page_size: int) -> tuple[list[Customer], int]:
        filters: list[Any] = []
        if q:
            filters.append(or_(Customer.name.like(f"%{q}%"), Customer.code.like(f"%{q}%")))
        if level:
            filters.append(Customer.level == level)
        return self.list(filters=filters, order_by=[Customer.id.desc()], page=page, page_size=page_size)


class CarrierRepository(BaseRepository[Carrier]):
    model = Carrier


class VehicleRepository(BaseRepository[Vehicle]):
    model = Vehicle


class DriverRepository(BaseRepository[Driver]):
    model = Driver


class SlaRuleRepository(BaseRepository[SlaRule]):
    model = SlaRule

    def list_active(self) -> list[SlaRule]:
        return self.all(filters=[SlaRule.is_active.is_(True)], order_by=[SlaRule.priority.asc(), SlaRule.id.asc()])


# --- 订单与轨迹 --------------------------------------------------------------
class OrderRepository(BaseRepository[Order]):
    model = Order

    def get_by_no(self, order_no: str) -> Order | None:
        return self.get_by(order_no=order_no)

    def search(
        self,
        *,
        status: str | None = None,
        customer_id: int | None = None,
        order_no: str | None = None,
        created_from: Any = None,
        created_to: Any = None,
        sort: list[Any] | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[Order], int]:
        filters: list[Any] = []
        if status:
            filters.append(Order.status == status)
        if customer_id:
            filters.append(Order.customer_id == customer_id)
        if order_no:
            filters.append(Order.order_no.like(f"%{order_no}%"))
        if created_from is not None:
            filters.append(Order.created_at >= created_from)
        if created_to is not None:
            filters.append(Order.created_at <= created_to)
        return self.list(filters=filters, order_by=sort or [Order.id.desc()], page=page, page_size=page_size)

    def count_in_transit(self) -> int:
        return self.count(Order.status.in_(["DISPATCHED", "IN_TRANSIT"]))

    def count_delivered_today(self, day_start: Any) -> int:
        return self.count(Order.delivered_at.is_not(None), Order.delivered_at >= day_start)


class TrackingEventRepository(BaseRepository[TrackingEvent]):
    model = TrackingEvent

    def list_for_order(self, order_id: int, limit: int = 100) -> list[TrackingEvent]:
        stmt = (
            self._scoped(select(TrackingEvent))
            .where(TrackingEvent.order_id == order_id)
            .order_by(TrackingEvent.occurred_at.desc(), TrackingEvent.id.desc())
            .limit(limit)
        )
        return list(self.session.scalars(stmt).unique())

    def latest(self, order_id: int) -> TrackingEvent | None:
        events = self.list_for_order(order_id, limit=1)
        return events[0] if events else None

    def list_for_order_asc(self, order_id: int, limit: int = 200) -> list[TrackingEvent]:
        stmt = (
            self._scoped(select(TrackingEvent))
            .where(TrackingEvent.order_id == order_id)
            .order_by(TrackingEvent.occurred_at.asc(), TrackingEvent.id.asc())
            .limit(limit)
        )
        return list(self.session.scalars(stmt).unique())


# --- 异常 -------------------------------------------------------------------
class ExceptionRepository(BaseRepository[ExceptionCase]):
    model = ExceptionCase

    def get_by_no(self, case_no: str) -> ExceptionCase | None:
        return self.get_by(case_no=case_no)

    def find_open_by_order(self, order_id: int) -> ExceptionCase | None:
        stmt = (
            self._scoped(select(ExceptionCase))
            .where(
                ExceptionCase.order_id == order_id,
                ExceptionCase.status.in_([str(status) for status in OPEN_EXCEPTION_STATUSES]),
            )
            .order_by(ExceptionCase.id.desc())
        )
        return self.session.scalars(stmt).unique().first()

    def next_sequence(self, prefix: str) -> int:
        """取已有 case_no 的**最大序号 + 1**（不能用 COUNT+1：seed 预置编号会让两者不一致）。"""
        rows = self.session.scalars(
            select(ExceptionCase.case_no).where(ExceptionCase.case_no.like(f"{prefix}%"))
        ).all()
        best = 0
        for value in rows:
            suffix = str(value)[len(prefix) :]
            if suffix.isdigit():
                best = max(best, int(suffix))
        return best + 1

    def search(
        self,
        *,
        status: str | None = None,
        level: str | None = None,
        type_: str | None = None,
        customer_id: int | None = None,
        sla_breached: bool | None = None,
        assigned_to: int | None = None,
        keyword: str | None = None,
        sort: list[Any] | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[ExceptionCase], int]:
        filters: list[Any] = []
        if status:
            filters.append(ExceptionCase.status == status)
        if level:
            filters.append(ExceptionCase.level == level)
        if type_:
            filters.append(ExceptionCase.type == type_)
        if customer_id:
            filters.append(ExceptionCase.customer_id == customer_id)
        if sla_breached is not None:
            filters.append(ExceptionCase.sla_breached.is_(sla_breached))
        if assigned_to:
            filters.append(ExceptionCase.assigned_to == assigned_to)
        if keyword:
            filters.append(
                or_(
                    ExceptionCase.case_no.like(f"%{keyword}%"),
                    ExceptionCase.impact_summary.like(f"%{keyword}%"),
                )
            )
        return self.list(
            filters=filters,
            order_by=sort or [ExceptionCase.risk_score.desc(), ExceptionCase.id.desc()],
            page=page,
            page_size=page_size,
        )

    def count_by_status(self) -> dict[str, int]:
        stmt = (
            self._scoped(select(ExceptionCase.status, func.count()))
            .group_by(ExceptionCase.status)
        )
        return {row[0]: int(row[1]) for row in self.session.execute(stmt).all()}

    def count_by_level(self) -> dict[str, int]:
        stmt = self._scoped(select(ExceptionCase.level, func.count())).group_by(ExceptionCase.level)
        return {row[0]: int(row[1]) for row in self.session.execute(stmt).all()}

    def list_open(self) -> list[ExceptionCase]:
        return self.all(filters=[ExceptionCase.status.in_([str(s) for s in OPEN_EXCEPTION_STATUSES])])


class ExceptionEventRepository(BaseRepository[ExceptionEvent]):
    model = ExceptionEvent

    def list_for_case(self, exception_id: int, page: int = 1, page_size: int = 50) -> tuple[list[ExceptionEvent], int]:
        filters = [ExceptionEvent.exception_id == exception_id]
        return self.list(filters=filters, order_by=[ExceptionEvent.id.asc()], page=page, page_size=page_size)


class CarrierMessageRepository(BaseRepository[CarrierMessage]):
    model = CarrierMessage

    def list_for_case(self, exception_id: int) -> list[CarrierMessage]:
        return self.all(filters=[CarrierMessage.exception_id == exception_id], order_by=[CarrierMessage.id.asc()])

    def latest_for_case(self, exception_id: int) -> CarrierMessage | None:
        rows = self.all(filters=[CarrierMessage.exception_id == exception_id], order_by=[CarrierMessage.id.desc()])
        return rows[0] if rows else None


class FollowupRepository(BaseRepository[FollowupTask]):
    model = FollowupTask

    def list_for_case(self, exception_id: int) -> list[FollowupTask]:
        return self.all(filters=[FollowupTask.exception_id == exception_id], order_by=[FollowupTask.id.asc()])

    def count_open(self, exception_id: int) -> int:
        return self.count(FollowupTask.exception_id == exception_id, FollowupTask.status == "OPEN")


class NotificationRepository(BaseRepository[Notification]):
    model = Notification

    def list_for_case(self, exception_id: int) -> list[Notification]:
        return self.all(filters=[Notification.exception_id == exception_id], order_by=[Notification.id.desc()])


# --- AI 与审批 ---------------------------------------------------------------
class AiAnalysisRepository(BaseRepository[AiAnalysis]):
    model = AiAnalysis

    def latest_for_case(self, exception_id: int) -> AiAnalysis | None:
        stmt = (
            self._scoped(select(AiAnalysis))
            .where(AiAnalysis.exception_id == exception_id)
            .order_by(AiAnalysis.id.desc())
        )
        return self.session.scalars(stmt).unique().first()

    def find_running(self, exception_id: int) -> AiAnalysis | None:
        stmt = self._scoped(select(AiAnalysis)).where(
            AiAnalysis.exception_id == exception_id,
            AiAnalysis.status.in_(["PENDING", "RUNNING"]),
        )
        return self.session.scalars(stmt).unique().first()

    def find_by_input_hash(self, exception_id: int, input_hash: str) -> AiAnalysis | None:
        stmt = (
            self._scoped(select(AiAnalysis))
            .where(
                AiAnalysis.exception_id == exception_id,
                AiAnalysis.input_hash == input_hash,
                AiAnalysis.status == "READY",
            )
            .order_by(AiAnalysis.id.desc())
        )
        return self.session.scalars(stmt).unique().first()

    def list_for_case(self, exception_id: int, limit: int = 10) -> list[AiAnalysis]:
        stmt = (
            self._scoped(select(AiAnalysis))
            .where(AiAnalysis.exception_id == exception_id)
            .order_by(AiAnalysis.id.desc())
            .limit(limit)
        )
        return list(self.session.scalars(stmt).unique())


class AiAnalysisStepRepository(BaseRepository[AiAnalysisStep]):
    model = AiAnalysisStep

    def list_for_analysis(self, analysis_id: int) -> list[AiAnalysisStep]:
        return self.all(filters=[AiAnalysisStep.analysis_id == analysis_id], order_by=[AiAnalysisStep.step_no.asc()])

    def next_step_no(self, analysis_id: int) -> int:
        stmt = select(func.max(AiAnalysisStep.step_no)).where(AiAnalysisStep.analysis_id == analysis_id)
        return int(self.session.scalar(stmt) or 0) + 1


class ApprovalRepository(BaseRepository[Approval]):
    model = Approval

    def list_for_case(self, exception_id: int) -> list[Approval]:
        return self.all(filters=[Approval.exception_id == exception_id], order_by=[Approval.id.asc()])

    def list_pending(self, exception_id: int | None = None) -> list[Approval]:
        filters: list[Any] = [Approval.status == "PENDING"]
        if exception_id is not None:
            filters.append(Approval.exception_id == exception_id)
        return self.all(filters=filters, order_by=[Approval.id.asc()])


# --- 审计与知识库 -------------------------------------------------------------
class AuditLogRepository(BaseRepository[AuditLog]):
    model = AuditLog

    def search(
        self,
        *,
        resource_type: str | None = None,
        resource_id: int | None = None,
        actor_id: int | None = None,
        action: str | None = None,
        occurred_from: Any = None,
        occurred_to: Any = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[AuditLog], int]:
        filters: list[Any] = []
        if resource_type:
            filters.append(AuditLog.resource_type == resource_type)
        if resource_id:
            filters.append(AuditLog.resource_id == resource_id)
        if actor_id:
            filters.append(AuditLog.actor_id == actor_id)
        if action:
            filters.append(AuditLog.action.like(f"%{action}%"))
        if occurred_from is not None:
            filters.append(AuditLog.occurred_at >= occurred_from)
        if occurred_to is not None:
            filters.append(AuditLog.occurred_at <= occurred_to)
        return self.list(filters=filters, order_by=[AuditLog.id.desc()], page=page, page_size=page_size)


class KnowledgeRepository(BaseRepository[KnowledgeDoc]):
    model = KnowledgeDoc

    def list_docs(self) -> list[KnowledgeDoc]:
        return self.all(order_by=[KnowledgeDoc.id.asc()])

    def get_by_code(self, doc_code: str) -> KnowledgeDoc | None:
        return self.get_by(doc_code=doc_code)

    def chunk_count(self, doc_id: int) -> int:
        stmt = select(func.count()).select_from(KnowledgeChunk).where(KnowledgeChunk.doc_id == doc_id)
        return int(self.session.scalar(stmt) or 0)

    def clear_chunks(self, doc_id: int) -> None:
        for chunk in self.session.scalars(select(KnowledgeChunk).where(KnowledgeChunk.doc_id == doc_id)).all():
            self.session.delete(chunk)
        self.session.flush()

    def add_chunk(self, chunk: KnowledgeChunk) -> KnowledgeChunk:
        self.session.add(chunk)
        self.session.flush()
        return chunk

    def search_chunks(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        """跨方言检索：MySQL 用 FULLTEXT(ngram) 优先，退化到 LIKE 打分。"""
        dialect = self.session.bind.dialect.name if self.session.bind is not None else "sqlite"
        rows: list[dict[str, Any]] = []
        if dialect == "mysql":
            sql = text(
                """
                SELECT c.id, c.doc_id, c.section_path, c.content, d.title AS doc_title,
                       MATCH(c.content) AGAINST (:q IN NATURAL LANGUAGE MODE) AS score
                FROM knowledge_chunk c
                JOIN knowledge_doc d ON d.id = c.doc_id
                WHERE MATCH(c.content) AGAINST (:q IN NATURAL LANGUAGE MODE)
                ORDER BY score DESC
                LIMIT :k
                """
            )
            try:
                result = self.session.execute(sql, {"q": query, "k": top_k}).mappings().all()
                rows = [dict(row) for row in result]
            except Exception:  # FULLTEXT 索引未建等 → 退化
                rows = []
        if not rows:
            like_sql = (
                select(
                    KnowledgeChunk.id,
                    KnowledgeChunk.doc_id,
                    KnowledgeChunk.section_path,
                    KnowledgeChunk.content,
                    KnowledgeDoc.title.label("doc_title"),
                )
                .join(KnowledgeDoc, KnowledgeDoc.id == KnowledgeChunk.doc_id)
                .where(cast(KnowledgeChunk.content, String).like(f"%{query}%"))
                .limit(top_k)
            )
            rows = [dict(row._mapping) for row in self.session.execute(like_sql).all()]
            for index, row in enumerate(rows):
                row["score"] = float(len(rows) - index)
        return rows


class Repos:
    """一次性拿全部仓储，避免 Service 里到处 new。"""

    def __init__(self, session: Session, workspace_id: int | None = None) -> None:
        self.session = session
        self.workspace_id = workspace_id
        self.users = UserRepository(session)
        self.workspaces = WorkspaceRepository(session)
        self.members = WorkspaceMemberRepository(session, workspace_id)
        self.settings = SettingRepository(session, workspace_id)
        self.customers = CustomerRepository(session, workspace_id)
        self.carriers = CarrierRepository(session, workspace_id)
        self.vehicles = VehicleRepository(session, workspace_id)
        self.drivers = DriverRepository(session, workspace_id)
        self.sla_rules = SlaRuleRepository(session, workspace_id)
        self.orders = OrderRepository(session, workspace_id)
        self.tracking = TrackingEventRepository(session, workspace_id)
        self.exceptions = ExceptionRepository(session, workspace_id)
        self.exception_events = ExceptionEventRepository(session, workspace_id)
        self.messages = CarrierMessageRepository(session, workspace_id)
        self.followups = FollowupRepository(session, workspace_id)
        self.notifications = NotificationRepository(session, workspace_id)
        self.analyses = AiAnalysisRepository(session, workspace_id)
        self.analysis_steps = AiAnalysisStepRepository(session, workspace_id)
        self.approvals = ApprovalRepository(session, workspace_id)
        self.audit = AuditLogRepository(session, workspace_id)
        self.knowledge = KnowledgeRepository(session, workspace_id)

    def get_or_404(self, repo: BaseRepository, obj_id: int, message: str = "资源不存在") -> Any:
        obj = repo.get(obj_id)
        if obj is None:
            raise not_found(message)
        return obj


__all__ = [
    "AiAnalysisRepository",
    "AiAnalysisStepRepository",
    "ApprovalRepository",
    "AuditLogRepository",
    "BaseRepository",
    "CarrierMessageRepository",
    "CarrierRepository",
    "CustomerRepository",
    "DriverRepository",
    "ExceptionEventRepository",
    "ExceptionRepository",
    "FollowupRepository",
    "KnowledgeRepository",
    "NotificationRepository",
    "OrderRepository",
    "Repos",
    "SettingRepository",
    "SlaRuleRepository",
    "TrackingEventRepository",
    "UserRepository",
    "VehicleRepository",
    "WorkspaceMemberRepository",
    "WorkspaceRepository",
]
