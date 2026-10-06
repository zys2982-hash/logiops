"""跨层契约测试（Lead 维护）。

前后端之间有一类约定**没有编译器保护**。2026-10-02 的真实事故：登录页写的演示账号域名是
`@demo.logiops`，seed 建的是 `@logiops.dev`，点演示账号必然 401 —— 本地跑后端测试、跑前端构建都发现不了。

2026-10-06 起口径变了（要部署到公网）：登录页**不再提供演示账号/口令**，所以原来的
"登录页必须覆盖 seed 全部账号"改成反向守卫：**登录页不得出现演示口令或演示账号**。
另外继续守住：前端不许再出现旧域名。
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


def test_login_page_does_not_leak_demo_credentials():
    """登录页**不得再出现演示口令或演示账号**（用户口径 2026-10-06：要部署到公网）。

    历史背景：原来登录页把表单预填成 `admin@logiops.dev / Demo@12345`，还带
    OWNER/ADMIN/OPERATOR/VIEWER 四个一键填充按钮与明文口令提示 —— 部署到公网等于公开口令。
    现在改成只靠输入；谁再把口令/一键填充加回来，这条会红。
    """
    from app.seed.catalog import DEMO_PASSWORD

    text = LOGIN_VIEW.read_text(encoding="utf-8")
    assert DEMO_PASSWORD not in text, "登录页又出现了演示口令（部署到公网等于公开口令）"
    leaked = sorted(email for email in set(EMAIL_RE.findall(text)) if email.endswith("@logiops.dev"))
    assert not leaked, f"登录页又出现了演示账号（点了就能进）：{leaked}"


def test_frontend_has_no_legacy_demo_domain():
    """前端任何文件都不该再出现旧域名 demo.logiops。"""
    offenders: list[str] = []
    for path in FRONTEND_SRC.rglob("*"):
        if not path.is_file() or path.suffix not in {".ts", ".vue", ".js", ".json", ".md"}:
            continue
        if LEGACY_DOMAIN in path.read_text(encoding="utf-8", errors="ignore"):
            offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, f"以下文件仍使用旧域名 {LEGACY_DOMAIN}：{offenders}"


def test_seed_demo_password_is_the_expected_demo_value():
    """演示口令常量仍要与约定一致（seed 用它建账号；上线前建议改掉，见 docs/10 §6）。

    注意：2026-10-06 起登录页**不再展示**该口令（见上一条），所以这里只校验常量本身。
    """
    from app.seed.catalog import DEMO_PASSWORD

    assert DEMO_PASSWORD == "Demo@12345"


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


ICONS_DIR = ROOT / "frontend" / "node_modules" / "@element-plus" / "icons-vue" / "dist" / "types" / "components"
# 侧边栏/卡片用 PascalCase 名字全局注册图标；包内文件名是 kebab-case
ICON_USAGE_RE = re.compile(r"(?:icon\s*[:=]\s*['\"]|:\s*icon[^\n]*?['\"])([A-Z][A-Za-z0-9]*)['\"]")


def _kebab(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "-", name).lower()


@pytest.mark.skipif(not ICONS_DIR.exists(), reason="未安装前端依赖（node_modules 缺失）")
def test_frontend_icon_names_exist():
    """前端引用的 Element Plus 图标名必须真实存在。

    真实事故：侧边栏"车辆"写成 `icon: 'Truck'`，而图标库里只有 `Van` ——
    名字解析不到时 `<component :is>` 静默渲染为空，界面没有任何报错，只是图标不见了。
    """
    available = {
        path.name.replace(".vue.d.ts", "")
        for path in ICONS_DIR.iterdir()
        if path.name.endswith(".vue.d.ts")
    }
    used: dict[str, str] = {}
    for path in FRONTEND_SRC.rglob("*.vue"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for match in ICON_USAGE_RE.finditer(text):
            used.setdefault(match.group(1), str(path.relative_to(ROOT)))
    missing = sorted(name for name in used if _kebab(name) not in available)
    assert not missing, "以下图标名在 @element-plus/icons-vue 中不存在（会静默不渲染）：" + ", ".join(
        f"{name}（{used[name]}）" for name in missing
    )
