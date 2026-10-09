"""知识库检索评测：**新打分（bigram BM25-lite + 标题加权）** vs **旧实现（MySQL FULLTEXT/LIKE）**。

为什么有这套评测（2026-10-08）：知识库检索是「AI 方案 4｜工具调用型 Agent」的前置 ——
`search_knowledge` 工具返回哪一节，模型就引用哪一节。改打分前先量，改完再量，避免凭感觉调参。

两套 query（用途不同，分开量）：
- KEYWORD：关键词式，**AI agent 实际会发的那种**（模板里就是 "延误" 这类短词）；
- NATURAL：人在搜索框里打的长句（"什么情况算违约"）。

跑法（backend 目录）：python ../scripts/kb_search_eval.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.db.session import get_sessionmaker
from app.repositories import Repos
from app.services import knowledge_index

TOP_K = 5

KEYWORD_CASES: list[tuple[str, list[str]]] = [
    ("延误", ["2.2 延误分钟"]),
    ("违约", ["2.3 违约判定"]),
    ("免责", ["2.5 免责与豁免"]),
    ("车辆故障", ["2.1 车辆故障分级", "2.2 现场处置流程", "概述"]),
    ("响应时限", ["3.2 响应时限"]),
    ("通知触发条件", ["5.1 通知触发条件"]),
    ("升级机制", ["1.5 升级机制"]),
    ("分级", ["1.3 等级与风险评分", "2.1 车辆故障分级"]),
    ("承诺送达", ["2.1 承诺送达时间"]),
    # 风险等级组成（2026-10-08 新增口径：AI 要按风险分构成给建议）
    ("风险评分", ["1.3 等级与风险评分"]),
    ("风险等级", ["1.3 等级与风险评分"]),
    ("预计到达时间", ["2.2 延误分钟", "2.4 预计到达时间"]),
]

NATURAL_CASES: list[tuple[str, list[str]]] = [
    ("延误分钟怎么算", ["2.2 延误分钟"]),
    ("什么情况算违约", ["2.3 违约判定"]),
    ("承诺送达时间怎么确定", ["2.1 承诺送达时间"]),
    ("台风封路导致延误能不能免责", ["2.5 免责与豁免"]),
    ("VIP 客户异常多久内必须响应", ["3.2 响应时限"]),
    ("货在路上坏了现场怎么处理", ["2.2 现场处置流程", "概述"]),
    ("什么时候必须给客户发通知", ["5.1 通知触发条件"]),
    ("异常分几个等级", ["1.3 等级与风险评分", "2.1 车辆故障分级"]),
    ("异常处置的完整流程是什么", ["1.4 处置流程"]),
    ("超时没确认会不会升级", ["1.5 升级机制"]),
    ("通知文案里能不能承诺赔偿金额", ["5.3 文案要求", "2.6 违约后的沟通要求"]),
    ("VIP 客户的告知有什么特殊要求", ["3.3 告知要求"]),
    # 风险等级组成
    ("风险等级是怎么算出来的", ["1.3 等级与风险评分"]),
    ("为什么这张单是高风险", ["1.3 等级与风险评分"]),
    ("客户等级会加多少风险分", ["1.3 等级与风险评分"]),
]


def old_baseline(session, query: str, top_k: int = TOP_K) -> list[str]:
    """复刻改动前的检索：MySQL 走 MATCH...AGAINST，其它方言退化 LIKE（返回 section_path 列表）。"""
    dialect = session.bind.dialect.name if session.bind is not None else "sqlite"
    if dialect == "mysql":
        sql = text(
            """
            SELECT c.section_path
            FROM knowledge_chunk c
            JOIN knowledge_doc d ON d.id = c.doc_id
            WHERE MATCH(c.content) AGAINST (:q IN NATURAL LANGUAGE MODE)
            ORDER BY MATCH(c.content) AGAINST (:q IN NATURAL LANGUAGE MODE) DESC
            LIMIT :k
            """
        )
        try:
            rows = session.execute(sql, {"q": query, "k": top_k}).all()
        except Exception:  # noqa: BLE001 - FULLTEXT 索引缺失等 → 与旧实现一致地退化
            rows = []
        if rows:
            return [row[0] or "" for row in rows]
    like = text(
        "SELECT c.section_path FROM knowledge_chunk c "
        "JOIN knowledge_doc d ON d.id = c.doc_id "
        "WHERE c.content LIKE :q LIMIT :k"
    )
    rows = session.execute(like, {"q": f"%{query}%", "k": top_k}).all()
    return [row[0] or "" for row in rows]


def rank_of(sections: list[str], expected: list[str]) -> int:
    for index, section in enumerate(sections, start=1):
        if any(key in section for key in expected):
            return index
    return 0


def evaluate(name: str, cases: list[tuple[str, list[str]]], session, repos: Repos) -> None:
    new_hits = {1: 0, 3: 0, 5: 0}
    old_hits = {1: 0, 3: 0, 5: 0}
    new_mrr = old_mrr = 0.0
    lines: list[str] = []
    for query, expected in cases:
        new_sections = [hit.get("section_path") or "" for hit in knowledge_index.search(repos, query, top_k=TOP_K)]
        old_sections = old_baseline(session, query)
        new_rank, old_rank = rank_of(new_sections, expected), rank_of(old_sections, expected)
        for k in new_hits:
            new_hits[k] += 1 if new_rank and new_rank <= k else 0
            old_hits[k] += 1 if old_rank and old_rank <= k else 0
        new_mrr += 1.0 / new_rank if new_rank else 0.0
        old_mrr += 1.0 / old_rank if old_rank else 0.0
        lines.append(
            f"  {query:<22} 新={new_rank or '✗':<3} 旧={old_rank or '✗':<3} "
            f"新top1={new_sections[0] if new_sections else '（无命中）'}"
        )
    n = len(cases)
    print(f"\n=== {name}（{n} 条）===")
    for line in lines:
        print(line)
    print(f"  新打分：hit@1={new_hits[1]}/{n}  hit@3={new_hits[3]}/{n}  hit@5={new_hits[5]}/{n}  MRR={new_mrr / n:.3f}")
    print(f"  旧实现：hit@1={old_hits[1]}/{n}  hit@3={old_hits[3]}/{n}  hit@5={old_hits[5]}/{n}  MRR={old_mrr / n:.3f}")


def main() -> None:
    session = get_sessionmaker()()
    repos = Repos(session, workspace_id=None)
    docs = repos.knowledge.all(order_by=[])
    print(f"语料：{len(docs)} 篇文档 / {sum(repos.knowledge.chunk_count(doc.id) for doc in docs)} 个分片")
    evaluate("KEYWORD（AI agent 会发的关键词）", KEYWORD_CASES, session, repos)
    evaluate("NATURAL（人在搜索框打的长句）", NATURAL_CASES, session, repos)
    session.close()


if __name__ == "__main__":
    main()
