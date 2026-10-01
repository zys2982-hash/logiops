"""知识库 schema（基线文档 §10.3【知识库】、§11.6）。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import OptUtcDateTime


class KnowledgeChunkOut(BaseModel):
    """分片明细：前端点"来源"时用来展示原文小节（§11.6 必须带来源）。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    chunk_no: int
    section_path: str | None = None
    content: str
    token_estimate: int | None = None


class KnowledgeDocOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    workspace_id: int | None = None
    doc_code: str
    title: str
    category: str | None = None
    version: str
    source_path: str | None = None
    status: str
    chunk_count: int = 0
    updated_at: OptUtcDateTime = None
    created_at: OptUtcDateTime = None


class KnowledgeDocDetail(BaseModel):
    """``GET /knowledge/docs/{id}``：文档元信息 + 分片原文。"""

    id: int
    doc_code: str
    title: str
    category: str | None = None
    version: str
    source_path: str | None = None
    status: str
    chunk_count: int = 0
    chunks: list[KnowledgeChunkOut] = Field(default_factory=list)


class KnowledgeDocsResponse(BaseModel):
    items: list[KnowledgeDocOut]
    total: int
    chunk_total: int = 0


class KnowledgeReindexResponse(BaseModel):
    """重建索引结果。知识库入口不可用时也返回 200，用 warning 说明（不阻塞业务）。"""

    indexed: int = 0
    docs: int = 0
    chunks: int = 0
    warning: str | None = None


__all__ = [
    "KnowledgeChunkOut",
    "KnowledgeDocDetail",
    "KnowledgeDocOut",
    "KnowledgeDocsResponse",
    "KnowledgeReindexResponse",
]
