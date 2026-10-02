"""跨层契约测试（Lead 维护）。

前后端之间有一类约定**没有编译器保护**：比如登录页"演示账号"的邮箱必须与后端 seed 创建的账号一致。
2026-10-02 的真实事故：登录页写的是 `@demo.logiops`，seed 建的是 `@logiops.dev`，
点演示账号必然 401——本地跑后端测试、跑前端构建都不会发现。

这里用两条断言把它钉住：① 登录页出现的账号必须覆盖 seed 全部演示账号；② 前端不许再出现旧域名。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FRONTEND_SRC = ROOT / "frontend" / "src"
LOGIN_VIEW = FRONTEND_SRC / "views" / "LoginView.vue"

LEGACY_DOMAIN = "demo.logiops"
EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.\w+")

pytestmark = pytest.mark.skipif(
    not LOGIN_VIEW.exists(), reason="未检出前端目录（后端独立检出时跳过跨层契约测试）"
)


def _seed_emails() -> set[str]:
    from app.seed.catalog import SEED_USERS

    return {spec["email"] for spec in SEED_USERS}


def test_login_page_covers_all_seed_accounts():
    """登录页的演示账号必须包含 seed 创建的全部账号（域名写错就会红）。"""
    emails = set(EMAIL_RE.findall(LOGIN_VIEW.read_text(encoding="utf-8")))
    missing = _seed_emails() - emails
    assert not missing, f"登录页缺少这些 seed 账号（点了会 401）：{sorted(missing)}"


def test_frontend_has_no_legacy_demo_domain():
    """前端任何文件都不该再出现旧域名 demo.logiops。"""
    offenders: list[str] = []
    for path in FRONTEND_SRC.rglob("*"):
        if not path.is_file() or path.suffix not in {".ts", ".vue", ".js", ".json", ".md"}:
            continue
        if LEGACY_DOMAIN in path.read_text(encoding="utf-8", errors="ignore"):
            offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, f"以下文件仍使用旧域名 {LEGACY_DOMAIN}：{offenders}"


def test_seed_demo_password_matches_docs():
    """演示口令必须与文档/前端一致（同样是跨层约定，写错就登不上）。"""
    from app.seed.catalog import DEMO_PASSWORD

    assert DEMO_PASSWORD == "Demo@12345"
    login_text = LOGIN_VIEW.read_text(encoding="utf-8")
    assert DEMO_PASSWORD in login_text, "登录页没有出现演示口令，前端与 seed 可能已不一致"


MAX_PAGE_SIZE = 100
PAGE_SIZE_RE = re.compile(r"page_size\s*[:=]\s*(\d+)")


def test_frontend_never_exceeds_max_page_size():
    """分页契约（基线 §10.1）：page_size ≤ 100，越界后端返回 422。

    真实事故：车辆页为了填司机下拉框写了 `page_size: 200`，于是一打开车辆页就弹
    "参数校验失败"（列表其实加载成功了，是并行的下拉框请求 422 冒出来的）。
    """
    offenders: list[str] = []
    for path in FRONTEND_SRC.rglob("*"):
        if not path.is_file() or path.suffix not in {".ts", ".vue"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for match in PAGE_SIZE_RE.finditer(text):
            if int(match.group(1)) > MAX_PAGE_SIZE:
                offenders.append(f"{path.relative_to(ROOT)}: page_size={match.group(1)}")
    assert not offenders, f"以下位置的分页参数超出契约上限 {MAX_PAGE_SIZE}：{offenders}"
