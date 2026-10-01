"""Replay：按 input_hash 回放 fixture；找不到时走确定性模板回退（§11.3 / §11.8）。

三级确定性（都 0 网络、0 API Key）：
1) 精确命中 `tests/fixtures/ai/*.json`（input_hash 相同）→ 回放录制输出与步骤，model=录制 model
2) 未命中 → 用 read_models 事实基线拼装输出，model="template"，is_replay=True
3) 模板也走 guard 校验（与 live 同一道闸门），保证"离线演示"与"线上"同口径
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.ai import templates
from app.ai.providers import ProviderResult, ToolCall
from app.ai.tools import ToolOutcome
from app.core.config import get_settings

if TYPE_CHECKING:  # pragma: no cover - 仅类型
    from app.ai.agent import AgentState

FIXTURE_SUFFIX = ".json"


@dataclass(frozen=True)
class ReplayFixture:
    task_type: str
    input_hash: str | None
    match_any: bool
    output: dict[str, Any] | None
    raw_output: str | None
    steps: list[dict[str, Any]]
    model: str
    tokens_in: int
    tokens_out: int
    path: Path


def resolve_replay_dir(directory: str | Path | None = None) -> Path:
    """settings.ai_replay_dir 默认相对 backend/ 解析（测试从 backend/ 运行）。"""
    if directory is not None:
        candidate = Path(directory)
        return candidate if candidate.is_absolute() else (Path.cwd() / candidate)
    raw = get_settings().ai_replay_dir
    candidate = Path(raw)
    if candidate.is_absolute():
        return candidate
    cwd_candidate = Path.cwd() / candidate
    if cwd_candidate.exists():
        return cwd_candidate
    backend_root = Path(__file__).resolve().parents[2]
    return backend_root / candidate


class ReplayStore:
    def __init__(self, directory: str | Path | None = None) -> None:
        self.directory = resolve_replay_dir(directory)
        self.errors: list[str] = []
        self._fixtures: list[ReplayFixture] | None = None

    # --- 读取 ---
    def fixtures(self, *, refresh: bool = False) -> list[ReplayFixture]:
        if self._fixtures is None or refresh:
            self._fixtures = self._load_all()
        return self._fixtures

    def _load_all(self) -> list[ReplayFixture]:
        self.errors = []
        if not self.directory.exists():
            return []
        fixtures: list[ReplayFixture] = []
        for path in sorted(self.directory.glob(f"*{FIXTURE_SUFFIX}")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                fixtures.append(_to_fixture(payload, path))
            except Exception as exc:  # noqa: BLE001 - fixture 损坏不应让演示崩溃
                self.errors.append(f"{path.name}: {exc}")
        return fixtures

    def find(self, task_type: str, input_hash: str | None) -> ReplayFixture | None:
        candidates = [item for item in self.fixtures() if item.task_type == task_type]
        if input_hash:
            for item in candidates:
                if item.input_hash and item.input_hash == input_hash:
                    return item
        for item in candidates:
            if item.match_any:
                return item
        return None

    def counts(self) -> dict[str, int]:
        result: dict[str, int] = {}
        for item in self.fixtures():
            result[item.task_type] = result.get(item.task_type, 0) + 1
        return result


def _to_fixture(payload: dict[str, Any], path: Path) -> ReplayFixture:
    task_type = str(payload.get("task_type") or "").upper()
    if not task_type:
        raise ValueError("fixture 缺少 task_type")
    output = payload.get("output")
    if output is not None and not isinstance(output, dict):
        raise ValueError("fixture.output 必须是对象")
    raw_output = payload.get("raw_output")
    if raw_output is None and output is not None:
        raw_output = json.dumps(output, ensure_ascii=False)
    steps = payload.get("steps") or []
    if not isinstance(steps, list):
        raise ValueError("fixture.steps 必须是数组")
    return ReplayFixture(
        task_type=task_type,
        input_hash=payload.get("input_hash"),
        match_any=bool(payload.get("match_any", False)),
        output=output,
        raw_output=raw_output,
        steps=[step for step in steps if isinstance(step, dict)],
        model=str(payload.get("model") or "replay"),
        tokens_in=int(payload.get("tokens_in") or 0),
        tokens_out=int(payload.get("tokens_out") or 0),
        path=path,
    )


# --- 默认（无录制）工具计划 ---------------------------------------------------
def knowledge_query(facts: dict[str, Any]) -> str:
    """知识库检索词：取能在五篇规范里稳定命中的短语。"""
    case = (facts.get("exception") or {}) if facts else {}
    exception_type = str(case.get("type") or "")
    if exception_type == "VEHICLE_BREAKDOWN":
        return "车辆故障"
    return "延误"


def default_t2_plan(facts: dict[str, Any]) -> list[ToolCall]:
    """默认 6 个只读工具：与 §11.3 示例一致（订单→轨迹→SLA→车辆→历史→规范）。"""
    facts = facts or {}
    case = facts.get("exception") or {}
    order = facts.get("order") or {}
    customer = facts.get("customer") or {}
    vehicle = facts.get("vehicle") or {}
    order_id = order.get("id") or case.get("order_id")
    customer_id = customer.get("id") or order.get("customer_id") or case.get("customer_id")
    vehicle_id = vehicle.get("id") or case.get("vehicle_id")

    calls: list[ToolCall] = []
    if order_id:
        calls.append(ToolCall("get_order", {"order_id": order_id}))
        calls.append(ToolCall("get_tracking_events", {"order_id": order_id, "limit": 10}))
    if customer_id:
        calls.append(ToolCall("get_customer_sla", {"customer_id": customer_id, "order_id": order_id}))
    if vehicle_id:
        calls.append(ToolCall("get_vehicle", {"vehicle_id": vehicle_id}))
    if customer_id:
        calls.append(ToolCall("get_exception_history", {"customer_id": customer_id, "days": 90}))
    calls.append(ToolCall("search_knowledge", {"query": knowledge_query(facts), "top_k": 5}))
    return calls


def plan_from_steps(steps: list[dict[str, Any]]) -> list[ToolCall]:
    calls: list[ToolCall] = []
    for step in steps:
        step_type = str(step.get("step_type") or "TOOL").upper()
        if step_type not in {"TOOL", ""}:
            continue
        name = step.get("tool_name") or step.get("name")
        if not name:
            continue
        args = step.get("args") or step.get("args_json") or step.get("input") or {}
        calls.append(ToolCall(str(name), args if isinstance(args, dict) else {}))
    return calls


class ReplayProvider:
    """AI_MODE=replay：fixture 优先，缺失则确定性模板回退。"""

    is_replay = True

    def __init__(self, store: ReplayStore | None = None, *, model_name: str = "template") -> None:
        self.store = store or ReplayStore()
        self.model_name = model_name
        self._resolved: dict[tuple[str, str | None], ReplayFixture | None] = {}
        self.last_fixture: ReplayFixture | None = None

    def fixture_for(self, task_type: str, input_hash: str | None) -> ReplayFixture | None:
        key = (task_type, input_hash)
        if key not in self._resolved:
            self._resolved[key] = self.store.find(task_type, input_hash)
        return self._resolved[key]

    # --- Provider 协议 ---
    def preset_plan(self, task_type: str, *, state: AgentState) -> list[ToolCall] | None:
        fixture = self.fixture_for(task_type, state.input_hash)
        if fixture is not None and fixture.steps:
            self.last_fixture = fixture
            return plan_from_steps(fixture.steps)
        if task_type == "ANALYZE_EXCEPTION":
            self.last_fixture = None
            return default_t2_plan(state.facts)
        return []

    def next_step(
        self,
        task_type: str,
        *,
        state: AgentState,
        repair_errors: list[str],
    ) -> ProviderResult:
        fixture = self.fixture_for(task_type, state.input_hash)
        if fixture is not None and fixture.output is not None:
            self.last_fixture = fixture
            return ProviderResult(
                final=fixture.output,
                raw_text=fixture.raw_output,
                model=fixture.model,
                tokens_in=fixture.tokens_in,
                tokens_out=fixture.tokens_out,
                is_replay=True,
            )
        output = self._template_output(task_type, state)
        return ProviderResult(
            final=output,
            raw_text=json.dumps(output, ensure_ascii=False, default=str),
            model="template",
            is_replay=True,
        )

    # --- 模板回退 ---
    def _template_output(self, task_type: str, state: AgentState) -> dict[str, Any]:
        if task_type == "ANALYZE_EXCEPTION":
            return templates.build_t2_output(
                facts=state.facts,
                tool_results=state.tool_results,
                knowledge=_knowledge_chunks(state.tool_results),
            )
        if task_type == "PARSE_MESSAGE":
            return templates.build_t1_output(
                raw_text=str(state.context.get("raw_text") or ""),
                now=state.context.get("now"),
                occurred_at=state.context.get("occurred_at"),
                expected_eta_at=state.context.get("expected_eta_at"),
            )
        if task_type == "DRAFT_NOTICE":
            return templates.build_t3_output(
                facts=state.facts,
                analysis=state.context.get("analysis") or {},
            )
        raise ValueError(f"未知任务类型：{task_type}")


def _knowledge_chunks(tool_results: dict[str, ToolOutcome]) -> list[dict[str, Any]]:
    outcome = tool_results.get("search_knowledge")
    if outcome is None or not isinstance(outcome.payload, list):
        return []
    return [chunk for chunk in outcome.payload if isinstance(chunk, dict)]


__all__ = [
    "FIXTURE_SUFFIX",
    "ReplayFixture",
    "ReplayProvider",
    "ReplayStore",
    "default_t2_plan",
    "knowledge_query",
    "plan_from_steps",
    "resolve_replay_dir",
]
