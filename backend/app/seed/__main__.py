"""Seed CLI（基线文档 §13.2）::

    uv run python -m app.seed --reset --demo              # 幂等重建固定演示数据
    uv run python -m app.seed --reset --demo --scenario case-a
    DATABASE_URL='sqlite+pysqlite:///./dev_seed.db' uv run python -m app.seed --reset --demo

--reset：先清空该工作区的业务数据再写入；--demo：写入完整演示规模（默认）。
执行后会打印各表条数与 CASE-A 的实算结果（ETA / 延误 / 等级 / 得分 / 命中规则）。
"""

from __future__ import annotations

import argparse
import json
import sys

from app.core.logging import setup_logging
from app.db.session import get_engine, session_scope
from app.models import Base


def _print_summary(summary: dict) -> None:
    clock = summary.get("clock") or {}
    print("=" * 78)
    print(f"工作区           : {summary['workspace_code']} (id={summary['workspace_id']})")
    print(f"场景 / 随机种子  : {summary['scenario']} / {summary['random_seed']}")
    print(
        f"业务基准日 / 时钟 : {clock.get('base_date')} / {clock.get('clock_mode')}"
        f"（offset={clock.get('offset_minutes')}min）"
    )
    print(f"业务时间(UTC)    : {summary['business_now_utc']}")
    print("-" * 78)
    print("各表条数：")
    for name, value in summary["counts"].items():
        print(f"  {name:<18}: {value}")
    print("-" * 78)
    print(f"订单状态分布     : {summary['orders_by_status']}")
    print(f"异常等级分布     : {summary['exceptions_by_level']}")
    print(f"异常状态分布     : {summary['exceptions_by_status']}")
    case_a = summary.get("case_a")
    if case_a:
        print("-" * 78)
        print("CASE-A 实算结果（全部由 app.rules 计算）：")
        print(f"  单号/客户      : {case_a['case_no']} / {case_a['order_no']} / {case_a['customer_code']}")
        print(f"  命中检测规则   : {case_a['detection_rule']}")
        print(f"  命中 SLA 规则  : {case_a['sla_rule']}")
        print(f"  ETA 算法       : {case_a['eta_method']}（{case_a['eta_detail']}）")
        print(f"  发车时间(UTC)  : {case_a.get('dispatched_at')}")
        print(f"  承诺到达(UTC)  : {case_a['promised_delivery_at']}")
        print(f"  预计到达(UTC)  : {case_a['expected_eta_at']}")
        print(f"  延误分钟       : {case_a['sla_delay_minutes']}")
        print(f"  SLA 违约       : {case_a['sla_breached']}")
        print(f"  风险等级/得分  : {case_a['level']} / {case_a['risk_score']}")
        print(f"  加分项         : {json.dumps(case_a['risk_factors'], ensure_ascii=False)}")
    if summary.get("knowledge"):
        print(f"知识库索引       : {summary['knowledge']}")
    print("=" * 78)


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    parser = argparse.ArgumentParser(prog="python -m app.seed", description="LogiOps 固定演示数据生成器")
    parser.add_argument("--reset", action="store_true", help="先清空该工作区业务数据再写入（幂等）")
    parser.add_argument("--demo", action="store_true", help="写入完整演示规模（默认行为）")
    parser.add_argument("--scenario", default="case-a", help="脚本化场景：case-a（默认）")
    parser.add_argument("--workspace-id", type=int, default=None, help="指定工作区 id；缺省用 code=SJ 的工作区")
    parser.add_argument("--no-knowledge", action="store_true", help="跳过知识库索引重建")
    parser.add_argument("--json", action="store_true", help="额外输出一行 JSON（便于脚本 diff）")
    args = parser.parse_args(argv)

    engine = get_engine()
    print(f"数据库方言：{engine.dialect.name}")
    # 幂等：只建缺失的表；--reset 由 reset_demo_data 负责"清空本工作区业务数据"
    Base.metadata.create_all(engine)
    try:
        from app.cli import ensure_fulltext_index

        ensure_fulltext_index(engine)
    except Exception as exc:  # pragma: no cover - 依赖 MySQL 版本
        print(f"[warn] FULLTEXT 索引创建跳过：{exc}")

    from app.seed import prepare_demo_clock, reset_demo_data, seed_all

    # 独立进程显式重置业务时钟到基准日（offset=0），避免沿用上一次 tick 的偏移；
    # 同时写入 system_setting['demo.base_date'] 作为跨进程交接值（见 prepare_demo_clock 说明）。
    clock_info = prepare_demo_clock()
    print(
        f"业务时钟已重置：base={clock_info['base_date']} mode={clock_info['clock_mode']} "
        f"offset={clock_info['offset_minutes']}min"
    )

    with session_scope() as session:
        if args.reset:
            summary = reset_demo_data(
                session,
                workspace_id=args.workspace_id,
                scenario=args.scenario,
                with_knowledge=not args.no_knowledge,
            )
        else:
            summary = seed_all(session, workspace_id=args.workspace_id)

    _print_summary(summary)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
