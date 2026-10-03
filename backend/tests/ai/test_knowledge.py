"""知识库：五篇规范、## 小节切分、checksum 幂等、检索必须带来源。"""

from __future__ import annotations

import shutil

from sqlalchemy import select

from app.models.ops import KnowledgeChunk, KnowledgeDoc
from app.repositories import Repos
from app.services.knowledge_index import (
    CHUNK_SOFT_LIMIT,
    KNOWLEDGE_DIR,
    list_docs,
    load_documents,
    parse_markdown,
    reindex_all,
    search,
)

EXPECTED_TITLES = {
    "01-exception-handling": "异常处理规范",
    "02-customer-sla": "客户 SLA 规则",
    "03-vip-service": "VIP 客户服务规则",
    "04-vehicle-breakdown": "车辆故障处理规范",
    "05-customer-notice": "客户通知规范",
}


def _chunk_ids(session) -> list[int]:
    return list(session.scalars(select(KnowledgeChunk.id).order_by(KnowledgeChunk.id)))


def _doc_checksum(session, doc_code: str) -> str:
    return session.scalars(select(KnowledgeDoc.checksum).where(KnowledgeDoc.doc_code == doc_code)).one()


def test_five_knowledge_docs_exist_with_expected_titles_and_length():
    files = sorted(KNOWLEDGE_DIR.glob("*.md"))
    assert len(files) == 5
    for path in files:
        lines = path.read_text(encoding="utf-8").splitlines()
        assert 60 <= len(lines) <= 150, f"{path.name} 行数 {len(lines)} 不在 60–150"
        assert EXPECTED_TITLES[path.stem] in lines[0]


def test_parse_markdown_keeps_section_path():
    path = KNOWLEDGE_DIR / "04-vehicle-breakdown.md"
    parsed = parse_markdown(path.read_text(encoding="utf-8"), doc_code=path.stem, source_path=path.name)
    assert parsed.title == "车辆故障处理规范"
    sections = [chunk.section_path for chunk in parsed.chunks]
    assert "2.1 车辆故障分级" in sections
    assert "概述" in sections
    assert all(len(chunk.content) <= CHUNK_SOFT_LIMIT for chunk in parsed.chunks)


def test_oversize_section_is_split_by_paragraph():
    body = "\n\n".join(["段落内容" * 60 for _ in range(6)])
    doc = parse_markdown(f"# 测试文档\n\n## 1.1 大节\n\n{body}\n", doc_code="t")
    assert len(doc.chunks) > 1
    assert all(chunk.section_path == "1.1 大节" for chunk in doc.chunks)
    assert all(len(chunk.content) <= CHUNK_SOFT_LIMIT for chunk in doc.chunks)


def test_reindex_all_is_idempotent(db_session, bootstrap):
    first = reindex_all(db_session, bootstrap["workspace_id"])
    assert first["docs"] == 5
    assert first["created"] == 5
    assert first["chunks"] == sum(len(doc.chunks) for doc in load_documents())

    repos = Repos(db_session, bootstrap["workspace_id"])
    docs_before = [(doc["doc_code"], doc["chunk_count"]) for doc in list_docs(repos)]
    chunk_ids_before = _chunk_ids(db_session)
    assert chunk_ids_before

    second = reindex_all(db_session, bootstrap["workspace_id"])
    assert second["skipped"] == 5
    assert second["created"] == 0 and second["updated"] == 0
    assert second["chunks"] == first["chunks"]

    assert [(doc["doc_code"], doc["chunk_count"]) for doc in list_docs(repos)] == docs_before
    assert _chunk_ids(db_session) == chunk_ids_before


def test_checksum_change_triggers_rebuild(db_session, tmp_path, bootstrap):
    for path in KNOWLEDGE_DIR.glob("*.md"):
        shutil.copy(path, tmp_path / path.name)
    first = reindex_all(db_session, bootstrap["workspace_id"], directory=tmp_path)
    assert first["created"] == 5
    checksum_before = _doc_checksum(db_session, "04-vehicle-breakdown")

    target = tmp_path / "04-vehicle-breakdown.md"
    target.write_text(
        target.read_text(encoding="utf-8") + "\n## 2.11 新增小节\n\n- 新增一行用于校验重建。\n",
        encoding="utf-8",
    )
    second = reindex_all(db_session, bootstrap["workspace_id"], directory=tmp_path)
    assert second["updated"] == 1
    assert second["skipped"] == 4
    assert _doc_checksum(db_session, "04-vehicle-breakdown") != checksum_before


def test_search_returns_source_and_no_hit_is_empty(db_session, repos, bootstrap):
    reindex_all(db_session, bootstrap["workspace_id"])
    rows = search(repos, "车辆故障", top_k=5)
    assert rows
    assert all(row["chunk_id"] and row["doc_title"] and row["section_path"] and row["content"] for row in rows)
    assert any("车辆故障" in row["content"] for row in rows)
    assert search(repos, "完全不存在的检索词XYZ", top_k=5) == []


def test_every_doc_has_chunks(db_session, repos, bootstrap):
    reindex_all(db_session, bootstrap["workspace_id"])
    overview = list_docs(repos)
    assert {doc["doc_code"] for doc in overview} == set(EXPECTED_TITLES)
    assert all(doc["chunk_count"] > 0 for doc in overview)
    assert all(doc["checksum"] for doc in overview)
