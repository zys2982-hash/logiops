"""仓储基类：所有查询强制注入 workspace 过滤（基线文档 §9.1 第 4 条）。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.core.errors import not_found
from app.db.base import Base


class BaseRepository[T: Base]:
    model: type[T]

    def __init__(self, session: Session, workspace_id: int | None = None) -> None:
        self.session = session
        self.workspace_id = workspace_id

    # --- 查询构造 ---------------------------------------------------------
    def query(self) -> Select:
        return self._scoped(select(self.model))

    def _scoped(self, stmt: Select) -> Select:
        model = self.model
        if self.workspace_id is not None and hasattr(model, "workspace_id"):
            stmt = stmt.where(model.workspace_id == self.workspace_id)
        if hasattr(model, "is_deleted"):
            stmt = stmt.where(model.is_deleted.is_(False))
        return stmt

    def _count(self, filters: list[Any] | None = None) -> int:
        stmt = self._scoped(select(func.count()).select_from(self.model))
        for condition in filters or []:
            stmt = stmt.where(condition)
        return int(self.session.scalar(stmt) or 0)

    # --- 单条 -------------------------------------------------------------
    def get(self, obj_id: int | None) -> T | None:
        if obj_id is None:
            return None
        stmt = self._scoped(select(self.model).where(self.model.id == obj_id))
        return self.session.scalars(stmt).unique().first()

    def get_or_404(self, obj_id: int | None, message: str = "资源不存在", **details: Any) -> T:
        obj = self.get(obj_id)
        if obj is None:
            raise not_found(message, **details)
        return obj

    def get_by(self, **filters: Any) -> T | None:
        stmt = self._scoped(select(self.model))
        for key, value in filters.items():
            stmt = stmt.where(getattr(self.model, key) == value)
        return self.session.scalars(stmt).unique().first()

    # --- 列表与分页 --------------------------------------------------------
    def list(
        self,
        *,
        filters: list[Any] | None = None,
        order_by: list[Any] | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[T], int]:
        stmt = self._scoped(select(self.model))
        for condition in filters or []:
            stmt = stmt.where(condition)
        total = self._count(filters)
        ordering = order_by or [self.model.id.desc()]
        stmt = stmt.order_by(*ordering).offset((max(page, 1) - 1) * page_size).limit(page_size)
        items = list(self.session.scalars(stmt).unique())
        return items, total

    def all(self, *, filters: list[Any] | None = None, order_by: list[Any] | None = None) -> list[T]:
        stmt = self._scoped(select(self.model))
        for condition in filters or []:
            stmt = stmt.where(condition)
        if order_by:
            stmt = stmt.order_by(*order_by)
        return list(self.session.scalars(stmt).unique())

    def count(self, *filters: Any) -> int:
        return self._count(list(filters))

    # --- 写入 -------------------------------------------------------------
    def add(self, obj: T) -> T:
        self.session.add(obj)
        self.session.flush()
        return obj

    def save(self, obj: T) -> T:
        self.session.add(obj)
        self.session.flush()
        return obj

    def delete(self, obj: T) -> None:
        self.session.delete(obj)
        self.session.flush()

    def soft_delete(self, obj: T) -> None:
        if hasattr(obj, "is_deleted"):
            obj.is_deleted = True  # type: ignore[attr-defined]
        self.session.add(obj)
        self.session.flush()
