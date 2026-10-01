"""知识库（基线文档 §10.3【知识库】、§11.6）。

- ``GET /knowledge/docs``：列出文档与分片数（含全租户共享文档 workspace_id=NULL）。
- ``GET /knowledge/docs/{id}``：文档元信息 + 分片原文（前端"来源"跳转用），不存在/跨租户一律 404。
- ``POST /knowledge/reindex``：从 Markdown 重建分片，入口 ``app.ai.knowledge_index.reindex_all``。
  该模块由 AI 智能体并行实现：不可用/失败时**不阻塞业务**，返回 200 + ``{"indexed": 0, "warning": ...}``。
"""

from __future__ import annotations

import inspect
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.api.deps import RequestContext, require
from app.core.errors import not_found
from app.core.permissions import Perm
from app.models.ops import KnowledgeChunk
from app.repositories import Repos
from app.schemas.knowledge import (
    KnowledgeChunkOut,
    KnowledgeDocDetail,
    KnowledgeDocOut,
    KnowledgeDocsResponse,
    KnowledgeReindexResponse,
)

router = APIRouter(prefix="/knowledge", tags=["knowledge"])

ViewCtx = Annotated[RequestContext, Depends(require(Perm.KNOWLEDGE_VIEW))]
ManageCtx = Annotated[RequestContext, Depends(require(Perm.KNOWLEDGE_MANAGE))]


def _global_repos(ctx: RequestContext) -> Repos:
    """知识库文档是全租户共享（workspace_id=NULL），不能用 ctx.repos 的租户过滤。"""
    return Repos(ctx.session, workspace_id=None)


def _visible_docs(ctx: RequestContext) -> list:
    docs = _global_repos(ctx).knowledge.all(order_by=[])
    return [doc for doc in docs if doc.workspace_id in (None, ctx.workspace_id)]


@router.get("/docs", response_model=KnowledgeDocsResponse, summary="知识库文档与分片数")
def list_docs(ctx: ViewCtx) -> KnowledgeDocsResponse:
    repos = _global_repos(ctx)
    items: list[KnowledgeDocOut] = []
    chunk_total = 0
    for doc in _visible_docs(ctx):
        count = repos.knowledge.chunk_count(doc.id)
        chunk_total += count
        item = KnowledgeDocOut.model_validate(doc)
        item.chunk_count = count
        items.append(item)
    return KnowledgeDocsResponse(items=items, total=len(items), chunk_total=chunk_total)


@router.get("/docs/{doc_id}", response_model=KnowledgeDocDetail, summary="文档详情 + 分片原文")
def get_doc(ctx: ViewCtx, doc_id: int) -> KnowledgeDocDetail:
    """按主键取文档；全租户共享（workspace_id=NULL）或本工作区的文档可见，其余一律 404（§9.1）。"""
    repos = _global_repos(ctx)
    doc = repos.knowledge.get(doc_id)
    if doc is None or (doc.workspace_id is not None and doc.workspace_id != ctx.workspace_id):
        raise not_found("知识库文档不存在", doc_id=doc_id)

    chunk_stmt = (
        select(KnowledgeChunk)
        .where(KnowledgeChunk.doc_id == doc.id)
        .order_by(KnowledgeChunk.chunk_no)
    )
    chunks = list(ctx.session.scalars(chunk_stmt))
    return KnowledgeDocDetail(
        id=doc.id,
        doc_code=doc.doc_code,
        title=doc.title,
        category=doc.category,
        version=doc.version,
        source_path=doc.source_path,
        status=doc.status,
        chunk_count=len(chunks),
        chunks=[KnowledgeChunkOut.model_validate(chunk) for chunk in chunks],
    )


def _invoke_reindex(session, workspace_id: int):
    """兼容未知签名的调用：``reindex_all()`` / ``(session)`` / ``(session, workspace_id=...)``。"""
    from app.ai.knowledge_index import reindex_all  # type: ignore[import-not-found]

    signature = inspect.signature(reindex_all)
    params = signature.parameters
    accepts_kwargs = any(param.kind is inspect.Parameter.VAR_KEYWORD for param in params.values())
    kwargs: dict[str, object] = {}
    if accepts_kwargs or "session" in params:
        kwargs["session"] = session
    if accepts_kwargs or "workspace_id" in params:
        kwargs["workspace_id"] = workspace_id
    return reindex_all(**kwargs)


@router.post("/reindex", response_model=KnowledgeReindexResponse, summary="重建知识库索引（ADMIN+）")
def reindex(ctx: ManageCtx) -> KnowledgeReindexResponse:
    warning: str | None = None
    indexed = 0
    docs_count = 0
    chunks_count = 0

    try:
        result = _invoke_reindex(ctx.session, ctx.workspace_id)
        ctx.session.flush()
        if isinstance(result, int):
            indexed = result
        elif isinstance(result, dict):
            indexed = int(result.get("indexed") or result.get("chunks") or 0)
            docs_count = int(result.get("docs") or 0)
            chunks_count = int(result.get("chunks") or indexed or 0)
            warning = result.get("warning")
        if not docs_count:
            docs_count = len(_visible_docs(ctx))
        if not chunks_count:
            chunks_count = sum(
                _global_repos(ctx).knowledge.chunk_count(doc.id) for doc in _visible_docs(ctx)
            )
        if not indexed:
            indexed = chunks_count
    except ImportError as exc:
        ctx.session.rollback()
        warning = f"知识库索引模块 app.ai.knowledge_index 不可用：{exc}"
    except Exception as exc:  # noqa: BLE001 - 知识库不可用不能阻塞业务
        ctx.session.rollback()
        warning = f"知识库重建失败：{type(exc).__name__}: {exc}"

    ctx.audit(
        "knowledge.reindex",
        resource_type="knowledge_doc",
        after={"indexed": indexed, "warning": warning},
        source="MANUAL",
    )
    return KnowledgeReindexResponse(indexed=indexed, docs=docs_count, chunks=chunks_count, warning=warning)


__all__ = ["router"]
