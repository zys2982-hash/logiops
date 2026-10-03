"""输出校验闸门：Schema 校验 + 事实一致性校验（§11.1 硬约束 3/4、§11.2 表 2）。

GuardError 由 agent 捕获 → 带错误信息修复重试 1 次 → 仍失败 → FAILED(AI_OUTPUT_INVALID)。
事实校验是"防幻觉"的演示核心：
  1) impact.delay_minutes 必须等于后端口径 sla_delay_minutes（±5min 容差）
  2) impact.sla_breached 必须等于后端计算结果
  3) suggestion.code ∈ ApprovalAction 白名单（§7.2 枚举）
  4) evidence_refs 的 id 必须来自本次工具返回/事实基线（防编造来源）
  5) T3 正文必须含订单号与 expected_eta，且时间/延误数字与事实一致
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import UnionType
from typing import Any, Union, get_args, get_origin

from pydantic import BaseModel, ValidationError

from app.ai import facts as F
from app.ai.tools import ToolOutcome, evidence_from_outcome
from app.core.clock import LOCAL_TZ, now_utc
from app.models.enums import ApprovalAction

DELAY_TOLERANCE_MINUTES = 5
EVIDENCE_TYPES = (
    "ORDER",
    "TRACKING_EVENT",
    "CUSTOMER",
    "SLA_RULE",
    "VEHICLE",
    "EXCEPTION",
    "CARRIER_MESSAGE",
    "KNOWLEDGE_CHUNK",
)


class GuardError(Exception):
    """校验失败：携带可回灌给模型的错误清单。"""

    def __init__(self, errors: list[str]) -> None:
        self.errors = [item for item in errors if item]
        super().__init__("；".join(self.errors) or "AI 输出校验失败")

    def as_feedback(self) -> str:
        return "\n".join(f"- {item}" for item in self.errors)


@dataclass
class EvidenceIndex:
    """本次运行"可引用 id 宇宙"：工具返回结果 + read_models 事实基线。"""

    _by_type: dict[str, set[str]] = field(default_factory=dict)

    def add(self, kind: str, value: Any) -> None:
        if value is None:
            return
        self._by_type.setdefault(kind, set()).add(str(value))

    def add_many(self, mapping: dict[str, set[str]]) -> None:
        for kind, values in mapping.items():
            for value in values:
                self.add(kind, value)

    def add_tool_outcome(self, outcome: ToolOutcome) -> None:
        if outcome.status != "OK":
            return
        self.add_many(evidence_from_outcome(outcome))

    def add_facts(self, facts: dict[str, Any] | None) -> None:
        facts = facts or {}
        self.add("ORDER", F.order_of(facts).get("id"))
        self.add("CUSTOMER", F.customer_of(facts).get("id"))
        self.add("SLA_RULE", F.sla_of(facts).get("rule_id"))
        self.add("VEHICLE", F.vehicle_of(facts).get("id"))
        self.add("EXCEPTION", F.case_of(facts).get("id"))
        self.add("CARRIER_MESSAGE", F.latest_message_of(facts).get("id"))
        for event in facts.get("tracking_events") or []:
            self.add("TRACKING_EVENT", (event or {}).get("id"))

    def contains(self, kind: str, value: Any) -> bool:
        return str(value) in self._by_type.get(str(kind).upper(), set())

    def universe(self) -> dict[str, set[str]]:
        return {kind: set(values) for kind, values in self._by_type.items()}

    def summary(self) -> str:
        parts = [f"{kind}={len(values)}" for kind, values in sorted(self._by_type.items())]
        return " ".join(parts) if parts else "空"


def _schema_errors(exc: ValidationError) -> list[str]:
    errors: list[str] = []
    for item in exc.errors():
        location = ".".join(str(part) for part in item.get("loc", []))
        errors.append(f"schema 校验失败：{location or '<root>'} {item.get('msg', '')}")
    return errors


# --- "话太多"的无损降级 ---------------------------------------------------------
def _max_length(pydantic_field: Any) -> int | None:
    for meta in getattr(pydantic_field, "metadata", ()):  # pydantic v2 的 MaxLen 约束
        limit = getattr(meta, "max_length", None)
        if isinstance(limit, int):
            return limit
    return None


def _unwrap(annotation: Any) -> Any:
    """只剥 ``Optional[X]``（``X | None``），**不要**动 ``list[X]``，否则嵌套模型就遍历不到了。"""
    if get_origin(annotation) in (Union, UnionType):
        args = [item for item in get_args(annotation) if item is not type(None)]
        return args[0] if len(args) == 1 else annotation
    return annotation


def _normalize_lengths(model: Any, payload: Any, notes: list[str]) -> Any:
    """按 schema 声明的长度上限做**无损降级**：超长文本截断、超长列表裁剪。

    动机（真实模型实测）：模型很容易把 summary/rationale 写超（上限 300 字却写了 500 字），
    若直接判 schema 失败，修复重试往往**仍然超长**（同样的提示词产生同样的输出），
    用户看到的就是"重新分析没有用"。截断只压缩表述、不改变事实与结论。
    """
    if not (isinstance(model, type) and issubclass(model, BaseModel)) or not isinstance(payload, dict):
        return payload
    out = dict(payload)
    for name, pydantic_field in model.model_fields.items():
        if name not in out:
            continue
        value = out[name]
        annotation = _unwrap(pydantic_field.annotation)
        limit = _max_length(pydantic_field)
        if isinstance(value, str):
            if limit and len(value) > limit:
                out[name] = value[: max(limit - 1, 1)].rstrip() + "…"
                notes.append(f"{name} 超过 {limit} 字，已截断")
        elif isinstance(value, list):
            args = get_args(annotation)
            item_type = _unwrap(args[0]) if args else Any
            if limit and len(value) > limit:
                out[name] = value[:limit]
                notes.append(f"{name} 超过 {limit} 条，已裁剪")
            if isinstance(item_type, type) and issubclass(item_type, BaseModel):
                out[name] = [_normalize_lengths(item_type, item, notes) for item in out[name]]
        elif isinstance(annotation, type) and issubclass(annotation, BaseModel) and isinstance(value, dict):
            out[name] = _normalize_lengths(annotation, value, notes)
    return out


# --- T1 ---------------------------------------------------------------------
def validate_t1(
    raw: dict[str, Any] | None,
    *,
    now: Any = None,
    occurred_at: Any = None,
) -> dict[str, Any]:
    from app.ai.schemas import ParseMessageOutput

    if not isinstance(raw, dict):
        raise GuardError(["输出不是 JSON 对象"])
    notes: list[str] = []
    raw = _normalize_lengths(ParseMessageOutput, raw, notes)
    try:
        output = ParseMessageOutput.model_validate(raw)
    except ValidationError as exc:
        raise GuardError(_schema_errors(exc)) from exc

    errors: list[str] = []
    if output.estimated_recovery_at is not None and not F.local_window_ok(
        output.estimated_recovery_at, occurred_at=occurred_at
    ):
        errors.append("estimated_recovery_at 必须晚于异常发生时间且不超过 48 小时之外")
    if not output.location.strip():
        errors.append("location 不能为空")
    if errors:
        raise GuardError(errors)
    result = output.model_dump(mode="json")
    if notes:
        result["_guard_notes"] = notes
    return result


# --- T2 ---------------------------------------------------------------------
def validate_t2(
    raw: dict[str, Any] | None,
    *,
    facts: dict[str, Any],
    evidence: EvidenceIndex | None = None,
) -> dict[str, Any]:
    from app.ai.schemas import AnalysisOutput

    if not isinstance(raw, dict):
        raise GuardError(["输出不是 JSON 对象"])
    notes: list[str] = []
    raw = _normalize_lengths(AnalysisOutput, raw, notes)
    try:
        output = AnalysisOutput.model_validate(raw)
    except ValidationError as exc:
        raise GuardError(_schema_errors(exc)) from exc

    errors: list[str] = []
    backend_delay = F.backend_delay_minutes(facts)
    delay_gap = None if backend_delay is None else abs(int(output.impact.delay_minutes) - int(backend_delay))
    if delay_gap is not None and delay_gap > DELAY_TOLERANCE_MINUTES:
        errors.append(
            f"impact.delay_minutes={output.impact.delay_minutes} 与后端 sla_delay_minutes={backend_delay} "
            f"不一致（容差 ±{DELAY_TOLERANCE_MINUTES} 分钟）"
        )

    backend_breached = F.backend_sla_breached(facts)
    if bool(output.impact.sla_breached) != backend_breached:
        errors.append(
            f"impact.sla_breached={output.impact.sla_breached} 与后端计算结果 {backend_breached} 不一致"
        )

    allowed_actions = {str(action) for action in ApprovalAction}
    for index, suggestion in enumerate(output.suggestions):
        if str(suggestion.code) not in allowed_actions:
            errors.append(f"suggestions[{index}].code={suggestion.code} 不在 ApprovalAction 白名单内")

    index_ = evidence or EvidenceIndex()
    if not index_.universe():
        index_.add_facts(facts)
    for ref in output.evidence_refs:
        kind = str(ref.type).upper()
        if kind not in EVIDENCE_TYPES:
            errors.append(f"evidence_refs.type={ref.type} 不是允许的来源类型")
        elif not index_.contains(kind, ref.id):
            errors.append(
                f"evidence_refs 引用了不存在的来源：{kind}#{ref.id}（防编造来源）"
            )

    if errors:
        raise GuardError(errors)
    result = output.model_dump(mode="json")
    if notes:
        result["_guard_notes"] = notes
    return result


# --- T3 ---------------------------------------------------------------------
def validate_t3(raw: dict[str, Any] | None, *, facts: dict[str, Any]) -> dict[str, Any]:
    from app.ai.schemas import NoticeOutput

    if not isinstance(raw, dict):
        raise GuardError(["输出不是 JSON 对象"])
    notes: list[str] = []
    raw = _normalize_lengths(NoticeOutput, raw, notes)
    try:
        output = NoticeOutput.model_validate(raw)
    except ValidationError as exc:
        raise GuardError(_schema_errors(exc)) from exc

    errors: list[str] = []
    case = F.case_of(facts)
    order = F.order_of(facts)
    order_no = str(order.get("order_no") or "")
    expected_eta = case.get("expected_eta_at") or F.sla_of(facts).get("expected_eta_at")
    promised = case.get("promised_delivery_at") or F.sla_of(facts).get("promised_delivery_at")
    content = output.content

    if order_no and order_no not in content.replace(" ", ""):
        errors.append(f"content 必须包含订单号 {order_no}")

    expected_keys = F.allowed_datetime_keys([expected_eta])
    allowed_keys = F.allowed_datetime_keys([expected_eta, promised, facts.get("now") or now_utc()])
    spans = F.extract_datetime_spans(content)

    eta_found = any(
        kind in {"full", "md"} and key in expected_keys[kind] for kind, _, key in spans
    )
    if not eta_found and expected_eta:
        # 允许"当天只用时分"的写法
        expected_dt = F.parse_any_dt(expected_eta)
        now_dt = F.parse_any_dt(facts.get("now") or now_utc())
        same_day = (
            expected_dt is not None
            and now_dt is not None
            and expected_dt.astimezone(LOCAL_TZ).date() == now_dt.astimezone(LOCAL_TZ).date()
        )
        eta_found = same_day and any(
            kind == "time" and key in expected_keys["time"] for kind, _, key in spans
        )
    if expected_eta and not eta_found:
        errors.append("content 必须包含与系统 expected_eta 一致的预计到达时间")

    for kind, raw_span, key in spans:
        if key not in allowed_keys[kind]:
            errors.append(f"content 出现事实之外的时间「{raw_span}」（事实时间只有 committed/ETA/当前时间）")

    if order_no:
        for found in F.order_numbers_in(content):
            if found != order_no:
                errors.append(f"content 出现未授权的订单号「{found}」")

    backend_delay = F.backend_delay_minutes(facts)
    if backend_delay is not None:
        for claim in F.delay_claims_minutes(content):
            if abs(claim - float(backend_delay)) > DELAY_TOLERANCE_MINUTES:
                errors.append(f"content 中的延误口径 {int(claim)} 分钟与后端 {backend_delay} 分钟不一致")

    if errors:
        raise GuardError(errors)
    result = output.model_dump(mode="json")
    if notes:
        result["_guard_notes"] = notes
    return result


__all__ = [
    "DELAY_TOLERANCE_MINUTES",
    "EVIDENCE_TYPES",
    "EvidenceIndex",
    "GuardError",
    "validate_t1",
    "validate_t2",
    "validate_t3",
]
