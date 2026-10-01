"""通用 schema：UTC 时间序列化、分页对象、错误模型、排序白名单（基线文档 §10.1）。

约定（唯一真相）：
- 时间出参一律 ISO8601 UTC，带 ``Z`` 后缀；入参接受 ``Z`` / ``+08:00`` / 无时区（按 UTC/本地解析）。
- 分页响应 ``{items, total, page, page_size}``（page 1 起，page_size ≤ 100）。
- 排序 ``?sort=-risk_score,created_at``，白名单字段，非法字段 → 422 VALIDATION_ERROR。
- 错误响应 ``{"error": {"code", "message", "details"}}``。
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, PlainSerializer

from app.core.errors import validation_error


# --- 时间序列化 --------------------------------------------------------------
def to_iso_z(value: datetime | None) -> str | None:
    """朴素 datetime 视为 UTC，输出统一带 Z（§7.3 / §10.1）。"""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


UtcDateTime = Annotated[datetime, PlainSerializer(to_iso_z, return_type=str, when_used="json")]
OptUtcDateTime = Annotated[datetime | None, PlainSerializer(to_iso_z, return_type=str | None, when_used="json")]


class ORMModel(BaseModel):
    """所有出参 schema 的基类：可直接吃 ORM 对象。"""

    model_config = ConfigDict(from_attributes=True)


# --- 分页 --------------------------------------------------------------------
class Page[T](BaseModel):
    items: list[T]
    total: int
    page: int
    page_size: int


def page_of[T](schema: type[T], rows: list[Any], total: int, page: int, page_size: int) -> Page[T]:
    """把 ORM 行 + 总数组装成分页响应。"""
    return Page[T](
        items=[schema.model_validate(row) for row in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


def patch_payload(payload: BaseModel) -> dict[str, Any]:
    """PATCH 语义：只取显式提供的字段，枚举转字符串，排除 expected_version（乐观锁单独处理）。"""
    data = payload.model_dump(exclude_unset=True, exclude={"expected_version"})
    return {key: (value.value if isinstance(value, Enum) else value) for key, value in data.items()}


# --- 排序白名单 ---------------------------------------------------------------
def parse_sort(sort: str | None, allowed: dict[str, Any], default: list[Any]) -> list[Any]:
    """把 ``-field,field`` 解析成 SQLAlchemy order_by 子句；非法字段抛 422。"""
    if not sort:
        return default
    clauses: list[Any] = []
    for raw in sort.split(","):
        token = raw.strip()
        if not token:
            continue
        descending = token.startswith("-")
        name = token[1:].strip() if descending else token
        column = allowed.get(name)
        if column is None:
            raise validation_error(
                f"不支持的排序字段：{name}",
                fields=[{"loc": "sort", "msg": f"不支持的排序字段：{name}", "allowed": sorted(allowed)}],
            )
        clauses.append(column.desc() if descending else column.asc())
    return clauses or default


# --- 错误模型 -----------------------------------------------------------------
class ErrorInfo(BaseModel):
    code: str
    message: str
    details: dict[str, Any] | None = None


class ErrorResponse(BaseModel):
    error: ErrorInfo


__all__ = [
    "ErrorInfo",
    "ErrorResponse",
    "ORMModel",
    "OptUtcDateTime",
    "Page",
    "UtcDateTime",
    "page_of",
    "parse_sort",
    "patch_payload",
    "to_iso_z",
]
