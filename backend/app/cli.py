"""命令行工具：建表 / 清库 / 建 FTS 索引。

用法（在 backend/ 下）：
    uv run python -m app.cli init-schema [--drop]
    uv run python -m app.cli reset-demo
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.core.logging import setup_logging
from app.db.session import get_engine
from app.models import Base

MYSQL_FULLTEXT_DDL = (
    "ALTER TABLE knowledge_chunk "
    "ADD FULLTEXT INDEX ft_knowledge_chunk_content (content) WITH PARSER ngram"
)


def ensure_fulltext_index(engine: Engine) -> None:
    """MySQL 专有：中文全文索引（ngram）。已存在则忽略。"""
    if engine.dialect.name != "mysql":
        return
    with engine.connect() as connection:
        exists = connection.execute(
            text(
                "SELECT COUNT(*) FROM information_schema.statistics "
                "WHERE table_schema = DATABASE() AND table_name = 'knowledge_chunk' "
                "AND index_name = 'ft_knowledge_chunk_content'"
            )
        ).scalar()
        if exists:
            print("   FULLTEXT 索引已存在")
            return
        try:
            connection.execute(text(MYSQL_FULLTEXT_DDL))
            connection.commit()
            print("   已创建 FULLTEXT 索引（ngram）")
        except Exception as exc:  # pragma: no cover - 依赖 MySQL 版本
            print(f"   [warn] FULLTEXT 索引创建失败（将退化为 LIKE 检索）：{exc}")


def cmd_init_schema(drop: bool) -> None:
    engine = get_engine()
    print(f"数据库方言：{engine.dialect.name}")
    if drop:
        Base.metadata.drop_all(engine)
        print("已删除全部表")
    Base.metadata.create_all(engine)
    print(f"已创建 {len(Base.metadata.tables)} 张表")
    ensure_fulltext_index(engine)


def cmd_reset_demo(scenario: str) -> None:
    from app.core.clock import state as clock_state
    from app.db.session import session_scope

    try:
        from app.seed import reset_demo_data
    except ImportError:
        print("seed 模块未实现（app/seed/__init__.py），跳过")
        return

    with session_scope() as session:
        workspaces = session.execute(text("SELECT id FROM workspace ORDER BY id LIMIT 1")).scalar()
        clock_state.reset()
        summary = reset_demo_data(session, workspace_id=workspaces, scenario=scenario)
        print(f"演示数据已重置：{summary}")


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    init_parser = sub.add_parser("init-schema", help="创建所有表（可选 --drop 先删）")
    init_parser.add_argument("--drop", action="store_true", help="先删除所有表")

    reset_parser = sub.add_parser("reset-demo", help="重置演示数据")
    reset_parser.add_argument("--scenario", default="case-a")

    args = parser.parse_args(argv)
    if args.command == "init-schema":
        cmd_init_schema(drop=args.drop)
    elif args.command == "reset-demo":
        cmd_reset_demo(scenario=args.scenario)
    return 0


if __name__ == "__main__":
    sys.exit(main())
