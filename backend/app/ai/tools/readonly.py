"""7 个只读工具（基线文档 §11.2 表 3）。

分层纪律：
- 业务数据一律经 `app.services.read_models`（只读视图）取得；
- 唯一例外是 `search_knowledge`，它调用 `Repos.knowledge.search_chunks`
  （知识库检索是仓储提供的检索接口，read_models 未提供该视图；同样无写操作、无直接 SQL）；
- 本包禁止 import `Session` / `Repository` 类，禁止拼 SQL（有静态单测守护）。

每个工具都返回 `ToolOutcome`，由 agent 负责写 `ai_analysis_step`。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.ai.errors import AiOutputInvalid
from app.core import labels
from app.services import read_models

MAX_TRACKING_LIMIT = 50
MAX_TOP_K = 5
MAX_HISTORY_DAYS = 90
DEFAULT_TRACKING_LIMIT = 20


@dataclass
class ToolContext:
    """工具运行上下文：只持有只读仓储引用，不持有 Session。"""

    repos: Any
    exception_id: int | None = None
    cache: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolOutcome:
    name: str
    args: dict[str, Any]
    payload: Any
    summary: str
    status: str = "OK"
    error: str | None = None


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    parameters: dict[str, Any]
    required: tuple[str, ...] = ()


TOOL_DEFINITIONS: tuple[ToolDefinition, ...] = (
    ToolDefinition(
        name="get_order",
        description="订单上下文：订单号/状态/客户/起终点/里程/承诺送达/ETA（order_id 用数据库主键 int）",
        parameters={"order_id": "int"},
        required=("order_id",),
    ),
    ToolDefinition(
        name="get_tracking_events",
        description="轨迹时间线：最近事件（时间/城市/类型/来源），limit≤50（order_id 必须是数据库主键 int）",
        parameters={"order_id": "int", "limit": "int"},
        required=("order_id",),
    ),
    ToolDefinition(
        name="get_customer",
        description="客户上下文：名称/等级/脱敏联系方式/通知偏好（customer_id 必须是数据库主键 int）",
        parameters={"customer_id": "int"},
        required=("customer_id",),
    ),
    ToolDefinition(
        name="get_customer_sla",
        description="SLA 规则：命中规则/偏移小时/允许延迟/承诺送达/延误分钟/是否违约（customer_id 用主键 int）",
        parameters={"customer_id": "int", "order_id": "int"},
        required=("customer_id",),
    ),
    ToolDefinition(
        name="get_vehicle",
        description="车辆状态：车牌/状态/承运商/当前城市（vehicle_id 必须是数据库主键 int，不是车牌）",
        parameters={"vehicle_id": "int"},
        required=("vehicle_id",),
    ),
    ToolDefinition(
        name="get_exception_history",
        description="历史异常：近 N 天条数/未结数/平均处理时长/最近 5 条摘要（days≤90；customer_id 用主键 int）",
        parameters={"customer_id": "int", "order_id": "int", "days": "int"},
        required=("customer_id",),
    ),
    ToolDefinition(
        name="search_knowledge",
        description="规范检索：返回带来源（doc_title/section_path/chunk_id/score）的片段，top_k≤5",
        parameters={"query": "str", "top_k": "int"},
        required=("query",),
    ),
)
TOOL_NAMES: tuple[str, ...] = tuple(definition.name for definition in TOOL_DEFINITIONS)


# --- 入参规范化 --------------------------------------------------------------
def _as_int(args: dict[str, Any], key: str, *, required: bool = True) -> int | None:
    value = args.get(key)
    if value is None or value == "":
        if required:
            raise ValueError(f"缺少必填入参 {key}")
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"入参 {key} 必须是整数：{value!r}") from exc


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def _time10(iso_text: str | None) -> str:
    """2026-09-30T11:00:00Z → 09-30 11:00（前端可读的摘要用）。"""
    if not iso_text:
        return "-"
    return iso_text.replace("T", " ")[:16].replace("Z", "").strip()[5:]


# --- 7 个工具实现 ------------------------------------------------------------
def _get_order(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    order_id = _as_int(args, "order_id")
    view = read_models.order_view(ctx.repos, order_id)  # type: ignore[arg-type]
    if view is None:
        return ToolOutcome("get_order", args, None, f"订单 {order_id} 不存在", status="ERROR", error="ORDER_NOT_FOUND")
    summary = (
        f"{view['order_no']} {view['origin_city']}→{view['dest_city']} "
        f"{labels.label(labels.ORDER_STATUS, view['status'])}"
    )
    return ToolOutcome("get_order", {"order_id": order_id}, view, summary)


def _get_tracking_events(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    order_id = _as_int(args, "order_id")
    limit = _clamp(int(args.get("limit") or DEFAULT_TRACKING_LIMIT), 1, MAX_TRACKING_LIMIT)
    events = read_models.tracking_view(ctx.repos, order_id, limit=limit)  # type: ignore[arg-type]
    last = events[0] if events else None
    if last is None:
        summary = "0 条轨迹"
    else:
        summary = f"{len(events)} 条轨迹，最后位置 {last['city']} {_time10(last['occurred_at'])}"
    return ToolOutcome("get_tracking_events", {"order_id": order_id, "limit": limit}, events, summary)


def _get_customer(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    customer_id = _as_int(args, "customer_id")
    view = read_models.customer_view(ctx.repos, customer_id)  # type: ignore[arg-type]
    if view is None:
        return ToolOutcome("get_customer", args, None, f"客户 {customer_id} 不存在", status="ERROR")
    summary = (
        f"{view['name']}（{labels.label(labels.CUSTOMER_LEVEL, view['level'])}）"
        f"通知偏好 {labels.label(labels.NOTIFY_PREF, view['notify_pref'], '未设置')}"
    )
    return ToolOutcome("get_customer", {"customer_id": customer_id}, view, summary)


def _get_customer_sla(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    customer_id = _as_int(args, "customer_id")
    order_id = _as_int(args, "order_id", required=False)
    view = read_models.sla_view(ctx.repos, customer_id=customer_id, order_id=order_id)  # type: ignore[arg-type]
    summary = (
        f"{labels.label(labels.SLA_SCOPE, view['scope_type'])}：{view['scope_value'] or '-'} "
        f"发车后 {view['deadline_offset_hours']}h，"
        f"允许延迟 {view['max_delay_minutes']}min，延误 {view['delay_minutes']}min"
    )
    return ToolOutcome("get_customer_sla", {"customer_id": customer_id, "order_id": order_id}, view, summary)


def _get_vehicle(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    vehicle_id = _as_int(args, "vehicle_id")
    view = read_models.vehicle_view(ctx.repos, vehicle_id)  # type: ignore[arg-type]
    if view is None:
        return ToolOutcome("get_vehicle", args, None, f"车辆 {vehicle_id} 不存在", status="ERROR")
    summary = f"{view['plate_no']} {view.get('status_label') or view['status']} 当前 {view['current_city'] or '-'}"
    return ToolOutcome("get_vehicle", {"vehicle_id": vehicle_id}, view, summary)


def _get_exception_history(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    customer_id = _as_int(args, "customer_id")
    days = _clamp(int(args.get("days") or MAX_HISTORY_DAYS), 1, MAX_HISTORY_DAYS)
    view = read_models.exception_history_view(ctx.repos, customer_id=customer_id, days=days)  # type: ignore[arg-type]
    summary = f"近 {days} 天 {view['total']} 次，未结 {view['open']} 次"
    return ToolOutcome(
        "get_exception_history",
        {"customer_id": customer_id, "days": days},
        view,
        summary,
    )


def _search_knowledge(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    query = str(args.get("query") or "").strip()
    if not query:
        return ToolOutcome("search_knowledge", args, [], "查询词为空", status="ERROR", error="EMPTY_QUERY")
    top_k = _clamp(int(args.get("top_k") or MAX_TOP_K), 1, MAX_TOP_K)
    rows = ctx.repos.knowledge.search_chunks(query, top_k=top_k)
    chunks = [
        {
            "chunk_id": row.get("id"),
            "doc_id": row.get("doc_id"),
            "doc_title": row.get("doc_title"),
            "section_path": row.get("section_path"),
            "content": row.get("content"),
            "score": row.get("score"),
        }
        for row in rows
    ]
    if not chunks:
        summary = "无命中（将提示未找到相关规范）"
    else:
        first = chunks[0]
        summary = f"命中 {len(chunks)} 条（{first['doc_title']}#{first['section_path']}）"
    return ToolOutcome("search_knowledge", {"query": query, "top_k": top_k}, chunks, summary)


_HANDLERS = {
    "get_order": _get_order,
    "get_tracking_events": _get_tracking_events,
    "get_customer": _get_customer,
    "get_customer_sla": _get_customer_sla,
    "get_vehicle": _get_vehicle,
    "get_exception_history": _get_exception_history,
    "search_knowledge": _search_knowledge,
}


def call_tool(name: str, args: dict[str, Any] | None, ctx: ToolContext) -> ToolOutcome:
    """执行白名单工具；越界工具直接判 AI 输出非法（有限循环终止）。"""
    if name not in _HANDLERS:
        raise AiOutputInvalid(
            f"模型请求了非白名单工具：{name}",
            {"tool": name, "allowed": list(TOOL_NAMES)},
        )
    safe_args = dict(args or {})
    try:
        return _HANDLERS[name](ctx, safe_args)
    except ValueError as exc:
        return ToolOutcome(name, safe_args, None, f"入参非法：{exc}", status="ERROR", error=str(exc))


def evidence_from_outcome(outcome: ToolOutcome) -> dict[str, set[str]]:
    """把工具返回结果折算成"可引用 id 宇宙"（防编造来源）。"""
    payload = outcome.payload
    found: dict[str, set[str]] = {}

    def _add(kind: str, value: Any) -> None:
        if value is None:
            return
        found.setdefault(kind, set()).add(str(value))

    if outcome.name == "get_order" and isinstance(payload, dict):
        _add("ORDER", payload.get("id"))
    elif outcome.name == "get_tracking_events" and isinstance(payload, list):
        for event in payload:
            _add("TRACKING_EVENT", (event or {}).get("id"))
    elif outcome.name == "get_customer" and isinstance(payload, dict):
        _add("CUSTOMER", payload.get("id"))
    elif outcome.name == "get_customer_sla" and isinstance(payload, dict):
        _add("SLA_RULE", payload.get("rule_id"))
    elif outcome.name == "get_vehicle" and isinstance(payload, dict):
        _add("VEHICLE", payload.get("id"))
    elif outcome.name == "search_knowledge" and isinstance(payload, list):
        for chunk in payload:
            _add("KNOWLEDGE_CHUNK", (chunk or {}).get("chunk_id"))
    return found


def tool_schema_for_llm() -> list[dict[str, Any]]:
    """OpenAI 兼容 function-calling 的 tools 参数（live 模式用）。"""
    type_map = {"int": "integer", "str": "string"}
    tools: list[dict[str, Any]] = []
    for definition in TOOL_DEFINITIONS:
        properties = {
            key: {"type": type_map.get(kind, "string"), "description": f"{definition.description}（{key}）"}
            for key, kind in definition.parameters.items()
        }
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": definition.name,
                    "description": definition.description,
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                        "required": list(definition.required),
                        "additionalProperties": False,
                    },
                },
            }
        )
    return tools


__all__ = [
    "MAX_HISTORY_DAYS",
    "MAX_TOP_K",
    "MAX_TRACKING_LIMIT",
    "TOOL_DEFINITIONS",
    "TOOL_NAMES",
    "ToolContext",
    "ToolDefinition",
    "ToolOutcome",
    "call_tool",
    "evidence_from_outcome",
    "tool_schema_for_llm",
]
