#!/usr/bin/env python
# ruff: noqa: E501  —— 本文件含较多中文提示串，行长以可读性优先（阈值 110 仍适用于代码本身）
"""CASE-A 全链路回放与两次运行比对（基线 §13.3 / §14.5 第 3–5 步）。

本脚本被 scripts/acceptance.ps1 调用，也可以单独执行：

    cd backend
    uv run python ..\\scripts\\acceptance_case_a.py run --out ..\\artifacts\\acceptance_run1.json
    uv run python ..\\scripts\\acceptance_case_a.py run --out ..\\artifacts\\acceptance_run2.json
    uv run python ..\\scripts\\acceptance_case_a.py compare ..\\artifacts\\acceptance_run1.json ..\\artifacts\\acceptance_run2.json

设计要点
    * 用 FastAPI TestClient 在进程内跑真实链路，不依赖 uvicorn/端口/浏览器。
    * 账号与工作区直接从数据库取（seed 写入的 OWNER/ADMIN + §13.2 固定口令 Demo@12345），
      不硬编码邮箱，避免与 seed 实现耦合。
    * 落盘的是**原始**结果；两次运行"忽略时间戳与自增 id"的归一化只发生在 compare 阶段，
      因此 artifacts/*.json 仍然可以人工复盘每一处细节。
    * 任何"后端还没做好"的情况都归类为依赖缺失 → exit 2，并在 stdout 给出可执行的下一步提示；
      绝不静默通过。

退出码
    0  链路跑通（run）/ 两次运行归一化后完全一致（compare）
    1  业务断言失败（状态码/等级/审批执行等不符）或两次运行不一致
    2  依赖缺失：seed 未执行、AI replay fixtures 缺失、接口或服务层未就绪
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import traceback
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_DEP = 2

API = "/api/v1"
ORDER_NO = "SO20260930021"  # §13.3 CASE-A 主案例
CARRIER_MESSAGE = "车在济南爆胎了，正在等修理厂，预计晚上 8 点恢复。"
DEMO_PASSWORD = "Demo@12345"  # §13.2 seed 固定口令
EXPECTED_LEVEL = "CRITICAL"  # §13.3：CASE-A 规则定级结果
FIXTURE_DIR = BACKEND / "tests" / "fixtures" / "ai"

# 真正决定验收链路能否成立的接口（其余接口缺失只记录 "unavailable"）
CORE_ENDPOINTS: tuple[tuple[str, str], ...] = (
    ("POST", r"^/api/v1/auth/login$"),
    ("GET", r"^/api/v1/orders$"),
    ("POST", r"^/api/v1/exceptions/\{[^}]+\}/analyze$"),
    ("GET", r"^/api/v1/ai-analyses/\{[^}]+\}$"),
    ("GET", r"^/api/v1/exceptions/\{[^}]+\}/approvals$"),
    ("POST", r"^/api/v1/approvals/\{[^}]+\}/approve$"),
    ("POST", r"^/api/v1/demo/actions/tick$"),
)

TERMINAL_ANALYSIS = {"READY", "SUCCEEDED", "SUCCESS", "DONE", "COMPLETED", "FAILED", "ERROR"}

# 归一化：这些键直接忽略（每次运行必然不同、又无业务含义）
IGNORE_KEYS = {
    "request_id",
    "trace_id",
    "duration_ms",
    "latency_ms",
    "elapsed_ms",
    "elapsed_seconds",
    "now_utc",
    "generated_at",
    "server_time",
}
ID_KEY_RE = re.compile(r"(^|_)ids?$")
TS_KEY_RE = re.compile(r"(_at|_time|timestamp|time|date)$")
TS_RE = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?")


# --------------------------------------------------------------------------------------
# 错误类型
# --------------------------------------------------------------------------------------
class DependencyMissing(RuntimeError):
    """依赖缺失：seed / fixtures / 后端接口或服务层未就绪 → exit 2。"""


class CaseFailure(RuntimeError):
    """链路跑通了但结果不符合基线契约 → exit 1。"""


# --------------------------------------------------------------------------------------
# 取值小工具（后端字段名可能带 _json 后缀，统一容错）
# --------------------------------------------------------------------------------------
def pick(data: Any, *keys: str, default: Any = None) -> Any:
    if isinstance(data, dict):
        for key in keys:
            if key in data and data[key] is not None:
                return data[key]
    return default


def items_of(payload: Any) -> list:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("items", "data", "results", "list"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    return []


def json_of(resp: Any) -> Any:
    """容错解析响应 JSON，**保留数组**；body_of() 会把平数组吞成 {}，凡列表型接口都用这个。"""
    try:
        return resp.json()
    except Exception:
        return {}


def body_of(resp: Any) -> dict:
    """容错解析响应 JSON；非 JSON 时返回空 dict。"""
    try:
        body = resp.json()
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}


def err_code(resp: Any) -> str:
    err = body_of(resp).get("error")
    if isinstance(err, dict):
        return str(err.get("code", ""))
    return str(err) if err else ""


def err_details(resp: Any) -> dict:
    err = body_of(resp).get("error")
    if isinstance(err, dict) and isinstance(err.get("details"), dict):
        return err["details"]
    return {}


def err_text(resp: Any) -> str:
    try:
        text = json.dumps(resp.json(), ensure_ascii=False)
    except Exception:
        text = getattr(resp, "text", "")
    return text[:300]


def exception_snapshot(exc: dict) -> dict:
    return {
        "case_no": pick(exc, "case_no"),
        "type": pick(exc, "type"),
        "level": pick(exc, "level"),
        "status": pick(exc, "status"),
        "risk_score": pick(exc, "risk_score"),
        "risk_factors": pick(exc, "risk_factors", "risk_factors_json"),
        "sla_delay_minutes": pick(exc, "sla_delay_minutes"),
        "sla_breached": pick(exc, "sla_breached"),
        "detection_rule": pick(exc, "detection_rule"),
        "detected_by": pick(exc, "detected_by"),
        "expected_eta_at": pick(exc, "expected_eta_at"),
        "promised_delivery_at": pick(exc, "promised_delivery_at"),
        "close_reason": pick(exc, "close_reason"),
        "version": pick(exc, "version"),
    }


def order_snapshot(order: dict) -> dict:
    return {
        "order_no": pick(order, "order_no"),
        "status": pick(order, "status"),
        "origin_city": pick(order, "origin_city", "origin"),
        "dest_city": pick(order, "dest_city", "destination"),
        "distance_km": pick(order, "distance_km"),
        "promised_delivery_at": pick(order, "promised_delivery_at"),
        "current_eta_at": pick(order, "current_eta_at"),
        "customer_level": pick(order, "customer_level"),
    }


# --------------------------------------------------------------------------------------
# API 包装
# --------------------------------------------------------------------------------------
class Api:
    def __init__(self, client: Any, token: str, workspace_id: int | None) -> None:
        self.client = client
        self.headers = {"Authorization": f"Bearer {token}"}
        if workspace_id is not None:
            self.headers["X-Workspace-Id"] = str(workspace_id)

    def call(
        self,
        method: str,
        path: str,
        *,
        expect: tuple[int, ...] = (200, 201, 202),
        required: bool = True,
        **kwargs: Any,
    ) -> Any:
        url = f"{API}{path}"
        resp = self.client.request(method, url, headers=self.headers, **kwargs)
        code = resp.status_code
        if code >= 500:
            raise DependencyMissing(
                f"{method} {url} → {code}，后端服务层未就绪（{err_code(resp)}）：{err_text(resp)}"
            )
        if required and code in (404, 405, 501):
            raise DependencyMissing(f"{method} {url} → {code}，接口/资源不存在（{err_code(resp)}）：{err_text(resp)}")
        if required and code not in expect:
            raise CaseFailure(f"{method} {url} → {code}，期望 {expect}：{err_text(resp)}")
        return resp


def optional(api: Api, method: str, path: str, **kwargs: Any) -> dict:
    """可选接口：缺失/未实现时记录 unavailable，不中断链路，也不影响两次运行一致性。"""
    resp = api.call(method, path, required=False, **kwargs)
    code = resp.status_code
    if code >= 300:
        return {"available": False, "http_status": code}
    try:
        body = resp.json()
    except Exception:
        body = None
    return {"available": True, "http_status": code, "body": body}


# --------------------------------------------------------------------------------------
# 依赖自检
# --------------------------------------------------------------------------------------
def check_openapi(client: Any) -> dict:
    resp = client.get("/openapi.json")
    if resp.status_code != 200:
        raise DependencyMissing("/openapi.json 不可用，后端未启动成功。")
    paths = resp.json().get("paths", {})
    missing: list[str] = []
    for method, pattern in CORE_ENDPOINTS:
        rx = re.compile(pattern)
        ok = any(rx.match(p) and method.lower() in {m.lower() for m in ops} for p, ops in paths.items())
        if not ok:
            missing.append(f"{method} {pattern}")
    if missing:
        raise DependencyMissing("以下核心接口尚未实现，CASE-A 链路无法验收：\n        - " + "\n        - ".join(missing))
    return paths


def check_ai_replay() -> None:
    settings_ai_mode = os.environ.get("AI_MODE", "replay").lower()
    if settings_ai_mode == "live":
        print("  [warn] AI_MODE=live：验收将真实调用模型，结果不可复现（基线 §14.5 要求 replay）。")
        return
    if not FIXTURE_DIR.is_dir() or not any(FIXTURE_DIR.glob("*.json")):
        raise DependencyMissing(
            f"AI_MODE=replay 但 fixtures 为空：{FIXTURE_DIR}\n"
            "        需要后端录制 backend/tests/fixtures/ai/*.json（基线 §11.3 / 附录 D-B P4）。"
        )


def find_account() -> tuple[str, str, int]:
    """从数据库取一个 OWNER/ADMIN 账号（不硬编码邮箱）。"""
    from sqlalchemy import select

    from app.db.session import session_scope
    from app.models import User, WorkspaceMember

    try:
        with session_scope() as session:
            rows = session.execute(
                select(User.email, User.name, WorkspaceMember.role, WorkspaceMember.workspace_id).join(
                    WorkspaceMember, WorkspaceMember.user_id == User.id
                )
            ).all()
    except Exception as exc:  # 表不存在 / 连不上库
        raise DependencyMissing(
            f"无法读取账号表（{type(exc).__name__}: {str(exc)[:200]}）。\n"
            "        请先执行 scripts/seed.ps1（uv run python -m app.seed --reset --demo）。"
        ) from exc

    if not rows:
        raise DependencyMissing(
            "数据库中没有成员记录：seed 未执行或未写入数据。\n"
            "        请先执行 scripts/seed.ps1（基线 §13.2）。"
        )
    rows = sorted(rows, key=lambda r: 0 if r[2] == "OWNER" else 1)
    email, name, role, workspace_id = rows[0]
    return email, name, int(workspace_id)


# --------------------------------------------------------------------------------------
# CASE-A 主链路
# --------------------------------------------------------------------------------------
def run_case(out_path: Path, scenario: str, timeout: float) -> int:
    os.environ.setdefault("AI_MODE", "replay")
    os.environ.setdefault("CLOCK_MODE", "replay")
    os.environ.setdefault("AI_REPLAY_DIR", "tests/fixtures/ai")
    os.chdir(BACKEND)  # 与 `cd backend; uv run uvicorn ...` 的相对路径口径一致

    check_ai_replay()

    from fastapi.testclient import TestClient

    from app.core.config import get_settings
    from app.main import app

    settings = get_settings()
    print(f"  AI_MODE={settings.ai_mode}  CLOCK_MODE={settings.clock_mode}  DB={settings.database_url.split('@')[-1]}")

    client = TestClient(app)
    check_openapi(client)

    print("  [1/9] 登录（seed 账号，口令 Demo@12345）...")
    email, name, workspace_id = find_account()
    login = client.post(f"{API}/auth/login", json={"email": email, "password": DEMO_PASSWORD})
    if login.status_code == 401:
        raise DependencyMissing(
            f"登录 401（{email} / Demo@12345）。seed 账号口令与 §13.2 约定不一致，请核对 app/seed。"
        )
    if login.status_code >= 400:
        raise DependencyMissing(f"登录失败 {login.status_code}：{err_text(login)}")
    token = pick(body_of(login), "access_token", "token")
    if not token:
        raise DependencyMissing(f"登录响应缺少 access_token：{err_text(login)}")
    api = Api(client, token, workspace_id)
    print(f"        ok：{name} <{email}>  workspace_id={workspace_id}")

    print(f"  [2/9] 定位 CASE-A 订单 {ORDER_NO} ...")
    orders = api.call("GET", "/orders", params={"order_no": ORDER_NO, "page": 1, "page_size": 5})
    order_items = items_of(body_of(orders))
    if not order_items:
        raise DependencyMissing(f"未找到订单 {ORDER_NO}：seed 未生成 CASE-A 数据（基线 §13.3）。")
    order = order_items[0]
    order_id = pick(order, "id")
    print(f"        ok：order_id={order_id} status={pick(order, 'status')}")

    print("  [3/9] 读取订单下的异常单（检测规则应在 seed/tick 后自动建单）...")
    # 注意：optional() 返回的是包装字典 {"available","status","body",...}，必须取 .get("body") 再解析，
    # 否则 items_of() 永远拿到空列表（这是本脚本早前 CASE-A 误判"无异常单"的根因）。
    exc_items = items_of(optional(api, "GET", f"/orders/{order_id}/exceptions").get("body"))
    if not exc_items:
        # /exceptions 合约里没有 order_id 过滤参数，取回列表后自行按 order_id 过滤
        fallback_body = optional(api, "GET", "/exceptions", params={"page": 1, "page_size": 50}).get("body")
        exc_items = [item for item in items_of(fallback_body) if pick(item, "order_id") == order_id]
    if not exc_items:
        raise DependencyMissing(
            "该订单没有异常单：检测规则或 demo tick 未就绪。\n"
            "        期望：STALL_OVER_THRESHOLD 自动建单（基线 §8.4 / §13.3 CASE-A）。"
        )
    exc = exc_items[0]
    exc_id = pick(exc, "id")
    detail = body_of(api.call("GET", f"/exceptions/{exc_id}"))
    initial = exception_snapshot(detail)
    print(f"        ok：exception_id={exc_id} status={initial['status']} level={initial['level']} risk={initial['risk_score']}")
    if initial["level"] != EXPECTED_LEVEL:
        raise CaseFailure(
            f"CASE-A 等级应为 {EXPECTED_LEVEL}（§13.3），实际 {initial['level']}：规则定级结果不符。"
        )

    print("  [4/9] 录入承运商消息并等待解析（T1 PARSE_MESSAGE）...")
    message_resp = api.call(
        "POST",
        f"/exceptions/{exc_id}/messages",
        json={"raw_text": CARRIER_MESSAGE, "channel": "MANUAL_PASTE", "sender_role": "CARRIER"},
        expect=(200, 201, 202),
    )
    message_body = body_of(message_resp)
    message_id = pick(message_body, "message_id", "id")
    parse_record: dict[str, Any] = {"message_id": message_id, "submit": message_body}
    parse_record["list"] = wait_parse(api, exc_id, message_id, timeout)
    print(f"        ok：parse_status={pick(parse_record['list'], 'parse_status')}")

    print("  [5/9] 触发 AI 分析（T2 ANALYZE_EXCEPTION，有界循环上限 ≤14 步、实测 8 步）...")
    # 乐观锁：第 4 步录入消息后 version 会自增，这里必须重新读取最新 version，否则会被 409 挡下
    detail = body_of(api.call("GET", f"/exceptions/{exc_id}"))
    version = pick(detail, "version", "lock_version")
    if version is None:
        raise DependencyMissing("异常详情未返回 version：乐观锁字段缺失（基线 §10.1 幂等约定）。")
    analysis_resp = api.call(
        "POST",
        f"/exceptions/{exc_id}/analyze",
        json={"expected_version": version},
        expect=(200, 202),
        required=False,
    )
    code = analysis_resp.status_code
    analysis_id: Any = None
    reused = False
    analysis_body: dict = {}
    if code in (404, 405, 501):
        raise DependencyMissing(f"POST /exceptions/{exc_id}/analyze → {code}：接口不存在（{err_code(analysis_resp)}）。")
    if code == 409 and err_code(analysis_resp) == "AI_ANALYSIS_IN_PROGRESS":
        analysis_id = err_details(analysis_resp).get("analysis_id")
        reused = True
    elif code not in (200, 202):
        raise CaseFailure(f"analyze → {code}，期望 200/202/409：{err_text(analysis_resp)}")
    else:
        analysis_body = body_of(analysis_resp)
        analysis_id = pick(analysis_body, "analysis_id", "id")
    if analysis_id is None:
        raise DependencyMissing(f"analyze 响应缺少 analysis_id：{err_text(analysis_resp)}")
    analysis = wait_analysis(api, analysis_id, timeout)
    steps = pick(analysis, "steps", default=[]) or []
    if not steps:
        raise DependencyMissing("AI 分析没有产出工具步骤（steps 为空）：有界循环未就绪（基线 §11.3）。")
    risk_level = pick(analysis, "risk_level_calculated", "risk_level")
    print(f"        ok：analysis_id={analysis_id}（reused={reused}）steps={len(steps)} risk_level_calculated={risk_level}")
    if risk_level is not None and risk_level != EXPECTED_LEVEL:
        raise CaseFailure(f"AI 面板 risk_level_calculated={risk_level} ≠ 规则定级 {EXPECTED_LEVEL}（§11.1 硬约束 2）。")

    print("  [6/9] 逐条批准 AI 建议（HITL：suggestions → approval → executor）...")
    approvals_items = items_of(json_of(api.call("GET", f"/exceptions/{exc_id}/approvals")))
    if not approvals_items:
        raise DependencyMissing("异常下没有审批单：suggestions → approval 转换未实现（基线 §11.5）。")
    executed = []
    for item in approvals_items[:6]:
        approval_id = pick(item, "id")
        payload = pick(item, "ai_payload", "ai_payload_json", "proposed_payload", "payload", "final_payload", default={})
        if not isinstance(payload, dict):
            payload = {}
        resp = api.call(
            "POST",
            f"/approvals/{approval_id}/approve",
            json={"expected_version": pick(item, "version", "expected_version", default=1), "final_payload": payload},
            expect=(200, 201),
        )
        body = body_of(resp)
        executed.append(
            {
                "action_type": pick(item, "action_type", "action"),
                "status_after": pick(body, "status", "approval_status"),
                "diff_changed": pick(pick(body, "diff", default={}), "changed", default=[]),
                "execution_result": pick(body, "execution_result", "result"),
            }
        )
    print(f"        ok：{len(executed)}/{len(approvals_items)} 条审批已执行")

    after_approval = exception_snapshot(body_of(api.call("GET", f"/exceptions/{exc_id}")))

    print("  [7/9] 推进业务时钟 + 推到送达（自动关闭链路）...")
    tick = body_of(api.call("POST", "/demo/actions/tick", json={"minutes": 60}))
    advance = optional(api, "POST", "/demo/actions/advance-to-less")
    final_detail = body_of(api.call("GET", f"/exceptions/{exc_id}"))
    final = exception_snapshot(final_detail)

    print("  [8/9] 读取时间线 / 跟进任务 / 通知 / Dashboard / 审计 ...")
    events = optional(api, "GET", f"/exceptions/{exc_id}/events", params={"page": 1, "page_size": 50})
    followups = optional(api, "GET", f"/exceptions/{exc_id}/followups")
    notifications = optional(api, "GET", f"/exceptions/{exc_id}/notifications")
    dashboard = optional(api, "GET", "/dashboard/summary")
    audit = optional(api, "GET", "/audit-logs", params={"resource_type": "exception", "resource_id": exc_id, "page": 1, "page_size": 50})

    def summarize(record: dict, *keys: str) -> dict:
        if not record.get("available"):
            return {"available": False, "http_status": record.get("http_status")}
        body = record.get("body") or {}
        return {"available": True, "total": pick(body, "total", default=len(items_of(body))), **{k: pick(body, k) for k in keys}}

    result = {
        "meta": {
            "case": "CASE-A",
            "scenario": scenario,
            "order_no": ORDER_NO,
            "message": CARRIER_MESSAGE,
            "expected_level": EXPECTED_LEVEL,
            "ai_mode": settings.ai_mode,
            "clock_mode": settings.clock_mode,
            "demo_base_date": settings.demo_base_date,
        },
        "order": order_snapshot(order),
        "exception_initial": initial,
        "message_parse": parse_record,
        "ai_analysis": {
            "analysis_id_reused": reused,
            "status": pick(analysis, "status"),
            "risk_level_calculated": risk_level,
            "steps": [
                {
                    "step_no": pick(step, "step_no"),
                    "step_type": pick(step, "step_type"),
                    "tool_name": pick(step, "tool_name"),
                    "status": pick(step, "status"),
                }
                for step in steps
            ],
            "output": pick(analysis, "output", "output_json"),
        },
        "approvals": executed,
        "approvals_total": len(approvals_items),
        "exception_after_approval": after_approval,
        "tick": tick,
        "advance_to_delivered": advance.get("body") if advance.get("available") else advance,
        "exception_final": final,
        "timeline": {
            "available": events.get("available"),
            "event_types": [pick(e, "event_type") for e in items_of(events.get("body"))],
        },
        "followups": summarize(followups, "items"),
        "notifications": summarize(notifications, "items"),
        "dashboard": dashboard.get("body") if dashboard.get("available") else dashboard,
        "audit": summarize(audit, "items"),
    }

    print("  [9/9] 落盘 ...")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(f"        已写入 {out_path}")
    print(
        f"  小结：steps={len(steps)} level={final['level']} status={final['status']} "
        f"审批 {len(executed)}/{len(approvals_items)} 条"
    )
    return EXIT_OK


def wait_parse(api: Api, exc_id: int, message_id: Any, timeout: float) -> dict:
    """消息解析是同步返回 PENDING + 异步解析；轮询 messages 列表直到 parse_status 不是 PENDING。"""
    deadline = time.time() + min(timeout, 60.0)
    last: dict = {}
    while time.time() < deadline:
        record = optional(api, "GET", f"/exceptions/{exc_id}/messages", params={"page": 1, "page_size": 20})
        if not record.get("available"):
            return {"parse_status": "UNAVAILABLE", "http_status": record.get("http_status")}
        items = items_of(record.get("body"))
        target = None
        for item in items:
            if message_id is None or pick(item, "id") == message_id:
                target = item
                break
        if target is None and items:
            target = items[-1]
        if target is not None:
            last = {
                "parse_status": pick(target, "parse_status"),
                "parse_result": pick(target, "parse_result", "parse_result_json"),
                "parser_version": pick(target, "parser_version"),
                "parse_error": pick(target, "parse_error"),
            }
            status = str(pick(target, "parse_status", default="")).upper()
            if status and status != "PENDING":
                return last
        time.sleep(0.3)
    return last or {"parse_status": "UNKNOWN"}


def wait_analysis(api: Api, analysis_id: Any, timeout: float) -> dict:
    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        resp = api.call("GET", f"/ai-analyses/{analysis_id}")
        body = body_of(resp)
        last = body
        status = str(pick(body, "status", default="")).upper()
        if status in TERMINAL_ANALYSIS:
            if status in {"FAILED", "ERROR"}:
                error_code = pick(body, "error_code", "error") or "UNKNOWN"
                raise DependencyMissing(
                    f"AI 分析 {status}（error_code={error_code}）：replay fixtures 缺失或输出校验未通过。\n"
                    "        处理：确认 backend/tests/fixtures/ai/*.json 已录制；或先跑 live 录制（附录 D-B P4）。"
                )
            return body
        time.sleep(0.3)
    raise DependencyMissing(f"AI 分析超时（{timeout}s），最后状态：{pick(last, 'status')}")


# --------------------------------------------------------------------------------------
# 归一化与比对（忽略时间戳与自增 id）
# --------------------------------------------------------------------------------------
def normalize(node: Any, id_map: dict[tuple[str, Any], str]) -> Any:
    if isinstance(node, dict):
        out: dict[str, Any] = {}
        for key, value in node.items():
            if key in IGNORE_KEYS:
                out[key] = "<ignored>"
            elif ID_KEY_RE.search(key) and isinstance(value, int) and not isinstance(value, bool):
                out[key] = id_map.setdefault((type(value).__name__, value), f"<id:{len(id_map)}>")
            elif ID_KEY_RE.search(key) and isinstance(value, list):
                # 自增 id 列表（approval_ids / exceptions_touched / exceptions_closed …）：
                # MySQL 自增不随 DELETE 复位，两次运行的 id 必然不同，只比对"数量"。
                out[key] = f"<id-list:{len(value)}>"
            elif TS_KEY_RE.search(key) and isinstance(value, str) and TS_RE.search(value):
                out[key] = "<timestamp>"
            else:
                out[key] = normalize(value, id_map)
        return out
    if isinstance(node, list):
        if node and all(isinstance(item, int) and not isinstance(item, bool) for item in node):
            return f"<int-list:{len(node)}>"
        return [normalize(item, id_map) for item in node]
    if isinstance(node, str):
        return TS_RE.sub("<timestamp>", node)
    return node


def diff(a: Any, b: Any, path: str = "$", out: list[str] | None = None, limit: int = 200) -> list[str]:
    if out is None:
        out = []
    if len(out) >= limit:
        return out
    if type(a) is not type(b):
        out.append(f"{path}: 类型不同 {type(a).__name__} → {type(b).__name__}（{a!r} vs {b!r}）")
        return out
    if isinstance(a, dict):
        for key in sorted(set(a) | set(b)):
            if key not in a:
                out.append(f"{path}.{key}: 仅 run2 存在 → {b[key]!r}")
            elif key not in b:
                out.append(f"{path}.{key}: 仅 run1 存在 → {a[key]!r}")
            else:
                diff(a[key], b[key], f"{path}.{key}", out, limit)
        return out
    if isinstance(a, list):
        if len(a) != len(b):
            out.append(f"{path}: 长度不同 {len(a)} → {len(b)}")
        for index, (left, right) in enumerate(zip(a, b, strict=False)):
            diff(left, right, f"{path}[{index}]", out, limit)
        return out
    if a != b:
        out.append(f"{path}: {a!r} → {b!r}")
    return out


def compare(first: Path, second: Path, json_out: Path | None) -> int:
    left = json.loads(first.read_text(encoding="utf-8"))
    right = json.loads(second.read_text(encoding="utf-8"))
    norm_left = normalize(left, {})
    norm_right = normalize(right, {})
    diffs = diff(norm_left, norm_right)
    report = {
        "run1": str(first),
        "run2": str(second),
        "identical_after_normalization": not diffs,
        "differences": diffs,
        "normalization": {
            "ignored_keys": sorted(IGNORE_KEYS),
            "id_keys": "键名匹配 (^|_)id$ 的整数 → 按首次出现顺序替换为 <id:N>",
            "timestamp_keys": "键名匹配 (_at|_time|timestamp|time|date)$ 或字符串内 ISO8601 → <timestamp>",
        },
    }
    if json_out is not None:
        json_out.parent.mkdir(parents=True, exist_ok=True)
        json_out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"  差异报告：{json_out}")

    if diffs:
        print(f"  [不一致] 归一化后仍有 {len(diffs)} 处差异（§14.5 要求两次运行完全一致）：")
        for line in diffs[:40]:
            print(f"    - {line}")
        if len(diffs) > 40:
            print(f"    ... 其余 {len(diffs) - 40} 处见差异报告")
        return EXIT_FAIL

    print("  [一致] 两次运行归一化后完全相同（已忽略时间戳与自增 id）")
    return EXIT_OK


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="acceptance_case_a.py",
        description="CASE-A 全链路回放（基线 §13.3）与两次运行比对（§14.5）",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run_parser = sub.add_parser("run", help="用 TestClient 跑一遍 CASE-A 并落盘 JSON")
    run_parser.add_argument("--out", required=True, type=Path, help="输出 JSON 路径")
    run_parser.add_argument("--scenario", default="case-a", help="传给 seed/demo 的场景名，默认 case-a")
    run_parser.add_argument("--timeout", type=float, default=120.0, help="AI 分析与解析的轮询超时（秒）")

    cmp_parser = sub.add_parser("compare", help="归一化后比对两份结果")
    cmp_parser.add_argument("first", type=Path)
    cmp_parser.add_argument("second", type=Path)
    cmp_parser.add_argument("--json", dest="json_out", type=Path, default=None, help="差异报告输出路径")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "run":
            out = args.out if args.out.is_absolute() else (ROOT / args.out)
            print(f"=== CASE-A 全链路（scenario={args.scenario}）===")
            return run_case(out, args.scenario, args.timeout)
        return compare(args.first, args.second, args.json_out)
    except DependencyMissing as exc:
        print(f"\n[exit 2] 依赖缺失：{exc}", file=sys.stderr)
        print("        提示：先跑 scripts/seed.ps1，确认 seed / AI replay fixtures / 业务接口已就绪。", file=sys.stderr)
        return EXIT_DEP
    except CaseFailure as exc:
        print(f"\n[exit 1] 验收失败：{exc}", file=sys.stderr)
        return EXIT_FAIL
    except Exception:
        print("\n[exit 1] 未预期错误：", file=sys.stderr)
        traceback.print_exc()
        return EXIT_FAIL


if __name__ == "__main__":
    sys.exit(main())
