"""FastAPI 应用入口。

启动：uv run uvicorn app.main:app --reload
"""

from __future__ import annotations

import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api.v1 import build_router
from app.core.config import current_clock_mode, get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import setup_logging

setup_logging()
settings = get_settings()

app = FastAPI(
    title="LogiOps 物流异常协同平台 API",
    description="面试演示项目：确定性规则定级 + LLM 受限生成 + 人工审批执行。",
    version=__version__,
    docs_url="/docs",
    redoc_url=None,
    openapi_url="/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-Id"],
)


@app.middleware("http")
async def add_request_id(request: Request, call_next):
    request_id = request.headers.get("X-Request-Id") or uuid.uuid4().hex
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-Id"] = request_id
    return response


register_exception_handlers(app)
app.include_router(build_router(), prefix=settings.api_prefix)


@app.get("/", tags=["system"], summary="服务信息")
def root() -> dict:
    return {
        "name": "LogiOps",
        "version": __version__,
        "docs": "/docs",
        "api_prefix": settings.api_prefix,
        "ai_mode": settings.ai_mode,
        "clock_mode": current_clock_mode(),
    }
