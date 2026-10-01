"""Prompt 模板与代码传入变量必须一致（§11.4 防漏传导致幻觉）。"""

from __future__ import annotations

import pytest

from app.ai.contexts import build_t1_context, build_t2_context, build_t3_context
from app.ai.errors import AiPromptError
from app.ai.prompts import (
    PROMPT_TASKS,
    TASK_TO_PROMPT,
    load_prompt,
    render,
    render_prompt,
    system_prompt,
)


@pytest.mark.parametrize(
    ("task", "context"),
    [
        ("t1_parse_message", build_t1_context(raw_text="车在济南爆胎了")),
        ("t2_analyze_exception", build_t2_context(facts={}, knowledge=[])),
        ("t3_draft_notice", build_t3_context(facts={}, analysis={})),
    ],
)
def test_template_variables_equal_code_context(task, context):
    template = load_prompt(task)
    assert template.variables == tuple(sorted(context))
    assert template.version == "v1"


def test_all_three_prompts_load_and_have_system_prompt():
    assert set(PROMPT_TASKS) == {"t1_parse_message", "t2_analyze_exception", "t3_draft_notice"}
    for task in PROMPT_TASKS:
        template = load_prompt(task)
        assert template.text.strip()
        assert template.variables
    for task_type, key in TASK_TO_PROMPT.items():
        assert system_prompt(task_type) == system_prompt(key)
        assert "JSON" in system_prompt(task_type)


def test_render_missing_variable_raises():
    template = load_prompt("t1_parse_message")
    with pytest.raises(AiPromptError) as exc:
        render(template, {"raw_text": "x"})
    assert "缺" in str(exc.value)


def test_render_extra_variable_raises():
    template = load_prompt("t1_parse_message")
    context = build_t1_context(raw_text="x")
    context["unexpected_key"] = "boom"
    with pytest.raises(AiPromptError) as exc:
        render(template, context)
    assert "多" in str(exc.value)


def test_render_substitutes_placeholders_and_keeps_json_braces():
    text, version = render_prompt(
        "t2_analyze_exception",
        build_t2_context(facts={"exception": {"sla_delay_minutes": 270, "sla_breached": True}}, knowledge=[]),
    )
    assert version == "v1"
    assert "270" in text
    assert "{sla_delay_minutes}" not in text
    assert '"summary"' in text  # JSON 示例的花括号未被误替换


def test_relative_time_examples_present_in_t1_prompt():
    template = load_prompt("t1_parse_message")
    assert "晚上" in template.text
    assert template.text.count("示例") >= 3
