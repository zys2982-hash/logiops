"""7 个只读工具：白名单、read_models 调用点、参数夹紧、证据折算、分层纪律。"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.ai.errors import AiOutputInvalid
from app.ai.tools import TOOL_NAMES, ToolContext, call_tool, evidence_from_outcome
from app.ai.tools.readonly import MAX_TOP_K, MAX_TRACKING_LIMIT
from app.services.knowledge_index import reindex_all, search

AI_ROOT = Path(__file__).resolve().parents[2] / "app" / "ai"
FORBIDDEN_ATTRS = {"execute", "scalars", "query", "select"}


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _imported_modules(tree: ast.Module) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            modules.add(module)
            modules.update(f"{module}.{alias.name}" for alias in node.names)
    return modules


def _assert_no_direct_sql(path: Path) -> None:
    tree = _parse(path)
    for module in _imported_modules(tree):
        assert not module.startswith("sqlalchemy"), f"{path.name} 引入了 {module}"
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            attr = getattr(node.func, "attr", "")
            assert attr not in FORBIDDEN_ATTRS, f"{path.name} 直接调用 {attr}()"


def _assert_tool_isolation(path: Path) -> None:
    for module in _imported_modules(_parse(path)):
        assert not module.startswith("app.repositories"), f"{path.name} 引入了 {module}"
        assert not module.startswith("app.models"), f"{path.name} 引入了 {module}"


def test_registry_has_exactly_seven_readonly_tools():
    assert TOOL_NAMES == (
        "get_order",
        "get_tracking_events",
        "get_customer",
        "get_customer_sla",
        "get_vehicle",
        "get_exception_history",
        "search_knowledge",
    )


def test_tools_do_not_import_session_or_repository():
    """静态纪律：tools 包不得 import Session / Repository / 模型，也不得直接操作 session。"""
    for path in sorted((AI_ROOT / "tools").glob("*.py")):
        _assert_no_direct_sql(path)
        _assert_tool_isolation(path)


def test_ai_layer_has_no_direct_sql():
    """AI 层整体禁止直接 SQL（入库/检索一律走 Repos 与 read_models）。"""
    for path in sorted(AI_ROOT.rglob("*.py")):
        _assert_no_direct_sql(path)


def test_whole_ai_package_forbids_repository_and_session_types():
    """整个 AI 包禁止 import Repository / sqlalchemy（含 Session 类型）。

    此前只有 tools/ 受这条约束，`app/ai/knowledge_index.py` 便带着
    `from app.repositories import Repos` + `session.commit()` 一路绿灯（已移到
    `app/services/knowledge_index.py`）。`session` 只能由外部注入——`runner.py` 里是 `session: Any`。
    """
    for path in sorted(AI_ROOT.rglob("*.py")):
        for module in _imported_modules(_parse(path)):
            assert not module.startswith("app.repositories"), f"{path.name} 引入了 {module}"
            assert not module.startswith("sqlalchemy"), f"{path.name} 引入了 {module}"


def test_knowledge_write_path_lives_outside_ai_package():
    """写库路径必须在 AI 包外：索引重建会写 knowledge_doc/chunk，不属于"只读 AI"。"""
    assert not (AI_ROOT / "knowledge_index.py").exists(), "knowledge_index 不得放回 app/ai/"
    from app.services import knowledge_index as service_index

    assert callable(service_index.reindex_all)


def test_tools_never_import_orm_models():
    """工具层必须经 read_models 取数，不得直接摸 ORM 模型（非工具代码才可用枚举/步骤模型）。"""
    for path in sorted((AI_ROOT / "tools").glob("*.py")):
        for module in _imported_modules(_parse(path)):
            assert not module.startswith("app.models"), f"{path.name} 引入了 {module}"


def test_tools_are_wired_to_read_models():
    text = (AI_ROOT / "tools" / "readonly.py").read_text(encoding="utf-8")
    for name in (
        "read_models.order_view",
        "read_models.tracking_view",
        "read_models.customer_view",
        "read_models.sla_view",
        "read_models.vehicle_view",
        "read_models.exception_history_view",
        "repos.knowledge.search_chunks",
    ):
        assert name in text, f"缺少调用点：{name}"


def test_unknown_tool_is_rejected(repos, case_a):
    with pytest.raises(AiOutputInvalid):
        call_tool("create_followup_task", {"x": 1}, ToolContext(repos=repos))


def test_order_tracking_vehicle_sla_tools_return_facts(repos, case_a):
    ctx = ToolContext(repos=repos, exception_id=case_a["exception_id"])
    order = call_tool("get_order", {"order_id": case_a["order_id"]}, ctx)
    assert order.status == "OK" and order.payload["order_no"] == "SO20260930021"
    # 摘要给人看 → 用中文（口径：不出现 IN_TRANSIT / REPAIRING 这类英文常量）
    assert "在途" in order.summary, order.summary

    tracking = call_tool("get_tracking_events", {"order_id": case_a["order_id"], "limit": 999}, ctx)
    assert tracking.payload[0]["event_type"] == "REPAIR_START"
    assert "3 条轨迹" in tracking.summary
    assert tracking.args["limit"] == MAX_TRACKING_LIMIT  # 夹紧上限

    vehicle = call_tool("get_vehicle", {"vehicle_id": case_a["case"].vehicle_id}, ctx)
    assert vehicle.payload["plate_no"] == "津A·12345"
    # 中文标签随状态走，且摘要里不出现英文常量
    assert vehicle.payload["status_label"] == "在途", vehicle.payload
    assert "在途" in vehicle.summary
    assert "IN_TRANSIT" not in vehicle.summary

    customer = call_tool("get_customer", {"customer_id": case_a["case"].customer_id}, ctx)
    assert customer.payload["level"] == "VIP"
    assert "****" in str(customer.payload["contact_phone"])

    sla = call_tool(
        "get_customer_sla",
        {"customer_id": case_a["case"].customer_id, "order_id": case_a["order_id"]},
        ctx,
    )
    assert sla.payload["delay_minutes"] == case_a["delay_minutes"]
    assert sla.payload["breached"] is True

    history = call_tool("get_exception_history", {"customer_id": case_a["case"].customer_id}, ctx)
    assert history.payload["total"] >= 1


def test_missing_row_returns_error_outcome_not_exception(repos):
    ctx = ToolContext(repos=repos)
    outcome = call_tool("get_order", {"order_id": 999999}, ctx)
    assert outcome.status == "ERROR"
    assert outcome.payload is None


def test_bad_args_returns_error_outcome(repos):
    ctx = ToolContext(repos=repos)
    outcome = call_tool("get_order", {}, ctx)
    assert outcome.status == "ERROR"
    assert "order_id" in str(outcome.error)


def test_search_knowledge_returns_source_with_topk_clamp(db_session, repos, bootstrap):
    reindex_all(db_session)
    for name in ("车辆故障", "延误"):
        outcome = call_tool("search_knowledge", {"query": name, "top_k": 99}, ToolContext(repos=repos))
        assert outcome.status == "OK"
        assert outcome.args["top_k"] == MAX_TOP_K
        assert outcome.payload, f"知识库未命中：{name}"
        first = outcome.payload[0]
        assert first["doc_title"] and first["section_path"] and first["chunk_id"]


def test_evidence_mapping_per_tool(db_session, repos, case_a):
    reindex_all(db_session)
    ctx = ToolContext(repos=repos, exception_id=case_a["exception_id"])
    order = call_tool("get_order", {"order_id": case_a["order_id"]}, ctx)
    assert evidence_from_outcome(order)["ORDER"] == {str(case_a["order_id"])}

    tracking = call_tool("get_tracking_events", {"order_id": case_a["order_id"]}, ctx)
    assert evidence_from_outcome(tracking)["TRACKING_EVENT"] == {str(item) for item in case_a["event_ids"]}

    chunks = call_tool("search_knowledge", {"query": "车辆故障"}, ctx)
    universe = evidence_from_outcome(chunks)
    assert universe["KNOWLEDGE_CHUNK"] == {str(chunk["chunk_id"]) for chunk in chunks.payload}


def test_knowledge_search_helper_returns_sources(db_session, repos):
    reindex_all(db_session)
    rows = search(repos, "车辆故障", top_k=3)
    assert rows
    assert {"chunk_id", "doc_title", "section_path", "content"} <= set(rows[0])
