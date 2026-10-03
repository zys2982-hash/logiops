"""知识库索引：app/knowledge/*.md → knowledge_doc / knowledge_chunk（§11.6）。

- 切分：按 `##` 小节切；超过 500 字再按段落切，chunk 保留 section_path
- 幂等：doc 级 checksum 相同且分片数一致 → 直接跳过（不重建、chunk_id 稳定）
- 索引：MySQL 下复用 `app.cli.ensure_fulltext_index`（FULLTEXT + ngram）
- 检索：`repos.knowledge.search_chunks`（MySQL FULLTEXT 优先，退化 LIKE），
  返回必须带来源 doc_title + section_path + chunk_id
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.models.ops import KnowledgeChunk, KnowledgeDoc
from app.repositories import Repos

KNOWLEDGE_DIR = Path(__file__).resolve().parents[1] / "knowledge"
CHUNK_SOFT_LIMIT = 500
DEFAULT_TOP_K = 5


@dataclass
class ParsedChunk:
    chunk_no: int
    section_path: str
    content: str
    checksum: str
    token_estimate: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "chunk_no": self.chunk_no,
            "section_path": self.section_path,
            "content": self.content,
            "checksum": self.checksum,
            "token_estimate": self.token_estimate,
        }


@dataclass
class ParsedDoc:
    doc_code: str
    title: str
    category: str | None
    source_path: str
    checksum: str
    chunks: list[ParsedChunk] = field(default_factory=list)


def _checksum(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 2)


def split_oversize(body: str, limit: int = CHUNK_SOFT_LIMIT) -> list[str]:
    """超长小节：先按空行分段累积，单段仍超长则按句子/长度硬切。"""
    body = body.strip()
    if not body:
        return []
    if len(body) <= limit:
        return [body]
    pieces: list[str] = []
    buffer = ""
    for paragraph in [item.strip() for item in body.split("\n\n") if item.strip()]:
        if len(paragraph) > limit:
            if buffer:
                pieces.append(buffer)
                buffer = ""
            pieces.extend(_hard_split(paragraph, limit))
            continue
        candidate = f"{buffer}\n\n{paragraph}" if buffer else paragraph
        if len(candidate) > limit:
            pieces.append(buffer)
            buffer = paragraph
        else:
            buffer = candidate
    if buffer:
        pieces.append(buffer)
    return pieces


def _hard_split(paragraph: str, limit: int) -> list[str]:
    pieces: list[str] = []
    remaining = paragraph
    while len(remaining) > limit:
        cut = remaining.rfind("。", 0, limit)
        if cut < limit // 2:
            cut = limit - 1
        pieces.append(remaining[: cut + 1].strip())
        remaining = remaining[cut + 1 :].strip()
    if remaining:
        pieces.append(remaining)
    return pieces


def parse_markdown(text: str, *, doc_code: str, source_path: str = "") -> ParsedDoc:
    """按 ## 小节切分；返回可直接入库的结构。"""
    lines = text.splitlines()
    title = doc_code
    title_found = False
    preamble: list[str] = []
    sections: list[tuple[str, list[str]]] = []
    current: list[str] | None = None

    for line in lines:
        if line.startswith("# ") and not title_found:
            title = line[2:].strip()
            title_found = True
            continue
        if line.startswith("## "):
            heading = line[3:].strip()
            current = []
            sections.append((heading, current))
            continue
        if current is None:
            preamble.append(line)
        else:
            current.append(line)

    chunks: list[ParsedChunk] = []

    def _push(section_path: str, body: str) -> None:
        for piece in split_oversize(body):
            chunks.append(
                ParsedChunk(
                    chunk_no=len(chunks) + 1,
                    section_path=section_path,
                    content=piece,
                    checksum=_checksum(piece),
                    token_estimate=_estimate_tokens(piece),
                )
            )

    if "".join(preamble).strip():
        _push("概述", "\n".join(preamble))
    for heading, body in sections:
        _push(heading, "\n".join(body))

    if not chunks:
        _push("概述", text)

    category = doc_code.split("-", 1)[1] if "-" in doc_code else doc_code
    return ParsedDoc(
        doc_code=doc_code,
        title=title,
        category=category,
        source_path=source_path,
        checksum=_checksum(text),
        chunks=chunks,
    )


def load_documents(directory: Path | str | None = None) -> list[ParsedDoc]:
    base = Path(directory) if directory else KNOWLEDGE_DIR
    docs: list[ParsedDoc] = []
    for path in sorted(base.glob("*.md")):
        docs.append(
            parse_markdown(
                path.read_text(encoding="utf-8"),
                doc_code=path.stem,
                source_path=path.name,
            )
        )
    return docs


def reindex_all(
    session: Any,
    workspace_id: int | None = None,
    *,
    directory: Path | str | None = None,
    engine: Any = None,
    ensure_index: bool = False,
) -> dict[str, Any]:
    """重建知识库索引；可重复执行（checksum 幂等）。

    注意 `ensure_index`：默认 **False**。索引 DDL（ALTER TABLE ADD FULLTEXT）属于 schema 阶段，
    由 alembic 迁移或 `python -m app.cli init-schema` 负责；在数据写入路径里做会与自己的
    未提交事务抢元数据锁而**自锁死**（MySQL lock_wait_timeout 默认一年，实测卡死 36 分钟）。
    只有确认调用方已提交事务、且确实要补索引时，才显式传 True。
    """
    repos = Repos(session, workspace_id)
    summary: dict[str, Any] = {
        "docs": 0,
        "created": 0,
        "updated": 0,
        "skipped": 0,
        "chunks": 0,
        "errors": [],
    }
    for parsed in load_documents(directory):
        summary["docs"] += 1
        existing = repos.knowledge.get_by_code(parsed.doc_code)
        same_version = (
            existing is not None
            and existing.checksum == parsed.checksum
            and repos.knowledge.chunk_count(existing.id) == len(parsed.chunks)
        )
        if same_version:
            summary["skipped"] += 1
            summary["chunks"] += len(parsed.chunks)
            continue
        if existing is None:
            existing = KnowledgeDoc(
                workspace_id=workspace_id,
                doc_code=parsed.doc_code,
                title=parsed.title,
                category=parsed.category,
                version="v1",
                source_path=parsed.source_path,
                status="ACTIVE",
                checksum=parsed.checksum,
            )
            session.add(existing)
            session.flush()
            summary["created"] += 1
        else:
            existing.title = parsed.title
            existing.category = parsed.category
            existing.source_path = parsed.source_path
            existing.status = "ACTIVE"
            existing.checksum = parsed.checksum
            summary["updated"] += 1
        repos.knowledge.clear_chunks(existing.id)
        for chunk in parsed.chunks:
            repos.knowledge.add_chunk(
                KnowledgeChunk(
                    doc_id=existing.id,
                    chunk_no=chunk.chunk_no,
                    section_path=chunk.section_path,
                    content=chunk.content,
                    token_estimate=chunk.token_estimate,
                    checksum=chunk.checksum,
                )
            )
        summary["chunks"] += len(parsed.chunks)

    session.flush()
    if ensure_index:
        # 显式补索引时，先把本会话的事务提交掉，释放它持有的元数据锁，否则 ALTER 会自锁死
        session.commit()
        bind = engine if engine is not None else session.get_bind()
        if bind is not None and getattr(bind, "dialect", None) is not None and bind.dialect.name == "mysql":
            try:  # pragma: no cover - 需要 MySQL
                from app.cli import ensure_fulltext_index

                ensure_fulltext_index(bind)
            except Exception as exc:  # noqa: BLE001 - 索引失败不阻塞（检索会退化 LIKE）
                summary["errors"].append(f"fulltext: {exc}")
    return summary


def list_docs(repos: Repos) -> list[dict[str, Any]]:
    docs = repos.knowledge.list_docs()
    return [
        {
            "id": doc.id,
            "doc_code": doc.doc_code,
            "title": doc.title,
            "category": doc.category,
            "checksum": doc.checksum,
            "chunk_count": repos.knowledge.chunk_count(doc.id),
        }
        for doc in docs
    ]


def search(repos: Repos, query: str, top_k: int = DEFAULT_TOP_K) -> list[dict[str, Any]]:
    """检索规范片段，返回必带来源（doc_title / section_path / chunk_id）。"""
    rows = repos.knowledge.search_chunks(query, top_k=top_k)
    return [
        {
            "chunk_id": row.get("id"),
            "doc_id": row.get("doc_id"),
            "doc_title": row.get("doc_title"),
            "section_path": row.get("section_path"),
            "content": row.get("content"),
            "score": row.get("score"),
        }
        for row in rows
    ]


__all__ = [
    "CHUNK_SOFT_LIMIT",
    "DEFAULT_TOP_K",
    "KNOWLEDGE_DIR",
    "ParsedChunk",
    "ParsedDoc",
    "list_docs",
    "load_documents",
    "parse_markdown",
    "reindex_all",
    "search",
    "split_oversize",
]
