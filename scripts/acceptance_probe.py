#!/usr/bin/env python
"""acceptance.ps1 / seed.ps1 的小工具探针（避免 PowerShell 5.1 引号与 stderr 陷阱）。

Windows PowerShell 5.1 在把 `-c "..."` 传给原生程序时会剥掉内层双引号，并且原生程序的
stderr 在 $ErrorActionPreference='Stop' 下会被升级成终止性错误。因此这里把需要引号和
异常处理的小片段统一做成子命令：**只打印干净的一行 stdout，失败用退出码表达**。

用法（在 backend/ 下，或任意目录——脚本自己把 backend 加进 sys.path）：

    uv run python ../scripts/acceptance_probe.py mask-db
    uv run python ../scripts/acceptance_probe.py check-db
    uv run python ../scripts/acceptance_probe.py check-seed
    uv run python ../scripts/acceptance_probe.py coverage <coverage.json>
    uv run python ../scripts/acceptance_probe.py summary <acceptance_run1.json>

退出码：0 成功；2 依赖缺失/检查失败（不打印 traceback，消息已足够定位）
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

EXIT_OK = 0
EXIT_DEP = 2


def mask_database_url() -> int:
    from app.core.config import get_settings

    url = get_settings().database_url
    masked = url
    if "://" in url and "@" in url:
        head, rest = url.split("://", 1)
        cred, host = rest.split("@", 1)
        user = cred.split(":")[0]
        masked = f"{head}://{user}:***@{host}"
    print(masked)
    return EXIT_OK


def check_db() -> int:
    from sqlalchemy import text

    from app.db.session import get_engine

    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 - 这里就是要吞掉异常、只回退出码
        print(f"数据库连接失败：{type(exc).__name__}: {str(exc)[:200]}")
        return EXIT_DEP
    print("db ok")
    return EXIT_OK


def check_seed() -> int:
    try:
        import app.seed  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        print(f"app.seed 不可用：{type(exc).__name__}: {str(exc)[:200]}")
        return EXIT_DEP
    print("app.seed ok")
    return EXIT_OK


def coverage(path: Path) -> int:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        print(round(float(data["totals"]["percent_covered"]), 1))
    except Exception as exc:  # noqa: BLE001
        print(f"读取覆盖率失败：{type(exc).__name__}: {str(exc)[:200]}")
        return EXIT_DEP
    return EXIT_OK


def junit(path: Path) -> int:
    """从 pytest 的 --junitxml 产物里读用例数。

    为什么不直接解析控制台输出：Windows PowerShell 5.1 在管道捕获时可能丢掉原生程序
    输出的最后一段（实测 pytest 结尾的 "N passed" 汇总行会丢），所以用例数以 JUnit XML 为准。
    """
    import xml.etree.ElementTree as ET

    try:
        root = ET.parse(path).getroot()
        suites = [root] if root.tag == "testsuite" else [node for node in root if node.tag == "testsuite"]
        tests = failures = errors = skipped = 0
        total_time = 0.0
        for suite in suites:
            tests += int(suite.get("tests") or 0)
            failures += int(suite.get("failures") or 0)
            errors += int(suite.get("errors") or 0)
            skipped += int(suite.get("skipped") or 0)
            total_time += float(suite.get("time") or 0)
        payload = {
            "tests": tests,
            "passed": max(tests - failures - errors - skipped, 0),
            "failures": failures,
            "errors": errors,
            "skipped": skipped,
            "time": round(total_time, 2),
        }
        print(json.dumps(payload, ensure_ascii=False))
    except Exception as exc:  # noqa: BLE001
        print(f"读取 junit.xml 失败：{type(exc).__name__}: {str(exc)[:200]}")
        return EXIT_DEP
    return EXIT_OK


def summary(path: Path) -> int:
    """提取验收汇总字段（§14.5 第 6 步：用例数/覆盖率/耗时/AI 步骤数/风险等级）。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        analysis = data.get("ai_analysis", {})
        final = data.get("exception_final", {})
        payload = {
            "steps": len(analysis.get("steps", [])),
            "risk_level": analysis.get("risk_level_calculated"),
            "final_level": final.get("level"),
            "final_status": final.get("status"),
            "approvals_total": data.get("approvals_total"),
            "approvals_executed": len(data.get("approvals", [])),
        }
        print(json.dumps(payload, ensure_ascii=False))
    except Exception as exc:  # noqa: BLE001
        print(f"读取 run1 产物失败：{type(exc).__name__}: {str(exc)[:200]}")
        return EXIT_DEP
    return EXIT_OK


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("用法：acceptance_probe.py mask-db|check-db|check-seed|")
        print("      coverage <file>|junit <file>|summary <file>")
        return EXIT_DEP
    command = argv[1]
    try:
        if command == "mask-db":
            return mask_database_url()
        if command == "check-db":
            return check_db()
        if command == "check-seed":
            return check_seed()
        if command == "coverage" and len(argv) >= 3:
            return coverage(Path(argv[2]))
        if command == "junit" and len(argv) >= 3:
            return junit(Path(argv[2]))
        if command == "summary" and len(argv) >= 3:
            return summary(Path(argv[2]))
        print(f"未知子命令或缺少参数：{' '.join(argv[1:])}")
        return EXIT_DEP
    except Exception as exc:  # noqa: BLE001 - 最后兜底：绝不让 traceback 走到 stderr
        print(f"探针执行失败：{type(exc).__name__}: {str(exc)[:200]}")
        return EXIT_DEP


if __name__ == "__main__":
    sys.exit(main(sys.argv))
