"""Prompt 模板加载与渲染（基线文档 §11.4）。

- 位置：app/ai/prompts/{t1_parse_message,t2_analyze_exception,t3_draft_notice}/v1.md
- 变量：`{placeholder}` 显式声明；`load_prompt()` 从文件反解变量集合，
  单测断言"集合 == 代码传入的上下文 key 集合"（漏传会导致幻觉）。
- 渲染：只替换 `{小写标识符}` 形态的占位符，JSON 示例里的 `{` `}` 原样保留。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.ai.errors import AiPromptError

PROMPT_ROOT = Path(__file__).resolve().parent / "prompts"
DEFAULT_PROMPT_VERSION = "v1"
PROMPT_TASKS: tuple[str, ...] = ("t1_parse_message", "t2_analyze_exception", "t3_draft_notice")

_PLACEHOLDER_RE = re.compile(r"\{([a-z][a-z0-9_]*)\}")

SYSTEM_PROMPTS: dict[str, str] = {
    "t1_parse_message": (
        "你是 LogiOps 承运商消息解析器。只输出一个 JSON 对象；"
        "不得编造未提供的信息，无依据填 null 并写入 missing_info。"
    ),
    "t2_analyze_exception": (
        "你是 LogiOps 异常分析助手。只输出一个 JSON 对象；"
        "只能复述工具返回的事实，不得编造数字/单号/时间/来源；"
        "风险等级由后端规则计算，你无权修改。"
    ),
    "t3_draft_notice": (
        "你是 LogiOps 客户通知文案助手。只输出一个 JSON 对象；"
        "正文必须包含订单号与系统给出的预计到达时间，不得出现未经授权的时间、单号或金额。"
    ),
}

TASK_TO_PROMPT: dict[str, str] = {
    "PARSE_MESSAGE": "t1_parse_message",
    "ANALYZE_EXCEPTION": "t2_analyze_exception",
    "DRAFT_NOTICE": "t3_draft_notice",
}


@dataclass(frozen=True)
class PromptTemplate:
    task: str
    version: str
    path: Path
    text: str
    variables: tuple[str, ...]

    def render(self, variables: Mapping[str, Any]) -> str:
        return render(self, variables)


def prompt_path(task: str, version: str = DEFAULT_PROMPT_VERSION, root: Path | None = None) -> Path:
    base = root or PROMPT_ROOT
    return base / task / f"{version}.md"


def load_prompt(task: str, version: str = DEFAULT_PROMPT_VERSION, root: Path | None = None) -> PromptTemplate:
    if task not in PROMPT_TASKS:
        raise AiPromptError(f"未知的 prompt 任务：{task}", {"allowed": list(PROMPT_TASKS)})
    path = prompt_path(task, version, root)
    if not path.exists():
        raise AiPromptError(f"prompt 模板不存在：{path}", {"task": task, "version": version})
    text = path.read_text(encoding="utf-8")
    variables = tuple(sorted(set(_PLACEHOLDER_RE.findall(text))))
    return PromptTemplate(task=task, version=version, path=path, text=text, variables=variables)


def render(template: PromptTemplate, variables: Mapping[str, Any]) -> str:
    """严格渲染：漏传 / 多传都报错（开发期立刻暴露，避免上线漏变量）。"""
    missing = [name for name in template.variables if name not in variables]
    extra = [name for name in variables if name not in template.variables]
    if missing or extra:
        raise AiPromptError(
            f"prompt 变量不一致（{template.task}/{template.version}）：缺 {missing} 多 {extra}",
            {"missing": missing, "extra": extra},
        )

    def _replace(match: re.Match[str]) -> str:
        name = match.group(1)
        value = variables[name]
        return "" if value is None else str(value)

    return _PLACEHOLDER_RE.sub(_replace, template.text)


def render_prompt(
    task: str,
    variables: Mapping[str, Any],
    version: str = DEFAULT_PROMPT_VERSION,
    root: Path | None = None,
) -> tuple[str, str]:
    """返回 (渲染后文本, prompt_version)。"""
    template = load_prompt(task, version, root)
    return render(template, variables), template.version


def system_prompt(task: str) -> str:
    """任务 system 约定（§11.4）。"""
    key = TASK_TO_PROMPT.get(task, task)
    return SYSTEM_PROMPTS.get(key, SYSTEM_PROMPTS["t2_analyze_exception"])


__all__ = [
    "DEFAULT_PROMPT_VERSION",
    "PROMPT_ROOT",
    "PROMPT_TASKS",
    "SYSTEM_PROMPTS",
    "TASK_TO_PROMPT",
    "PromptTemplate",
    "load_prompt",
    "prompt_path",
    "render",
    "render_prompt",
    "system_prompt",
]
