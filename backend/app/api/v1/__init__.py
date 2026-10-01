"""v1 路由聚合：按固定模块名自动发现，缺文件时只告警不崩（便于分工并行开发）。"""

from __future__ import annotations

import importlib
import logging

from fastapi import APIRouter

logger = logging.getLogger("logiops.api")

ROUTER_MODULES: tuple[str, ...] = (
    "health",
    "demo",
    "auth",
    "workspaces",
    "customers",
    "carriers",
    "vehicles",
    "drivers",
    "sla_rules",
    "orders",
    "tracking",
    "exceptions",
    "approvals",
    "followups",
    "notifications",
    "ai_analyses",
    "dashboard",
    "audit",
    "knowledge",
)


def build_router() -> APIRouter:
    root = APIRouter()
    for name in ROUTER_MODULES:
        module_path = f"app.api.v1.{name}"
        try:
            module = importlib.import_module(module_path)
        except ModuleNotFoundError as exc:
            if exc.name in {module_path, name}:
                logger.warning("路由模块未实现，跳过：%s", module_path)
                continue
            raise
        router = getattr(module, "router", None)
        if router is None:
            logger.warning("路由模块缺少 router 变量：%s", module_path)
            continue
        root.include_router(router)
    return root
