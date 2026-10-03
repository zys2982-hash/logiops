"""统一错误码与异常处理（基线文档 §10.2）。"""

from __future__ import annotations

import logging
from enum import StrEnum
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("logiops.errors")


class ErrorCode(StrEnum):
    AUTH_INVALID_CREDENTIALS = "AUTH_INVALID_CREDENTIALS"
    AUTH_TOKEN_EXPIRED = "AUTH_TOKEN_EXPIRED"
    AUTH_TOKEN_INVALID = "AUTH_TOKEN_INVALID"
    AUTH_TOKEN_MISSING = "AUTH_TOKEN_MISSING"
    PERM_DENIED = "PERM_DENIED"
    PERM_WORKSPACE_NOT_MEMBER = "PERM_WORKSPACE_NOT_MEMBER"
    RESOURCE_NOT_FOUND = "RESOURCE_NOT_FOUND"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    STATE_TRANSITION_INVALID = "STATE_TRANSITION_INVALID"
    OPTIMISTIC_LOCK_CONFLICT = "OPTIMISTIC_LOCK_CONFLICT"
    DUPLICATE_ENTITY = "DUPLICATE_ENTITY"
    OPEN_EXCEPTION_EXISTS = "OPEN_EXCEPTION_EXISTS"
    AI_ANALYSIS_IN_PROGRESS = "AI_ANALYSIS_IN_PROGRESS"
    APPROVAL_ALREADY_DECIDED = "APPROVAL_ALREADY_DECIDED"
    AI_OUTPUT_INVALID = "AI_OUTPUT_INVALID"
    LLM_UNAVAILABLE = "LLM_UNAVAILABLE"
    RATE_LIMITED = "RATE_LIMITED"
    DEMO_CLOCK_DISABLED = "DEMO_CLOCK_DISABLED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


HTTP_STATUS: dict[ErrorCode, int] = {
    ErrorCode.AUTH_INVALID_CREDENTIALS: 401,
    ErrorCode.AUTH_TOKEN_EXPIRED: 401,
    ErrorCode.AUTH_TOKEN_INVALID: 401,
    ErrorCode.AUTH_TOKEN_MISSING: 401,
    ErrorCode.PERM_DENIED: 403,
    ErrorCode.PERM_WORKSPACE_NOT_MEMBER: 403,
    ErrorCode.RESOURCE_NOT_FOUND: 404,
    ErrorCode.VALIDATION_ERROR: 422,
    ErrorCode.STATE_TRANSITION_INVALID: 409,
    ErrorCode.OPTIMISTIC_LOCK_CONFLICT: 409,
    ErrorCode.DUPLICATE_ENTITY: 409,
    ErrorCode.OPEN_EXCEPTION_EXISTS: 409,
    ErrorCode.AI_ANALYSIS_IN_PROGRESS: 409,
    ErrorCode.APPROVAL_ALREADY_DECIDED: 409,
    ErrorCode.AI_OUTPUT_INVALID: 502,
    ErrorCode.LLM_UNAVAILABLE: 503,
    ErrorCode.RATE_LIMITED: 429,
    ErrorCode.DEMO_CLOCK_DISABLED: 409,
    ErrorCode.INTERNAL_ERROR: 500,
}


def error_body(code: ErrorCode | str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"error": {"code": str(code), "message": message}}
    if details:
        body["error"]["details"] = details
    return body


class AppError(Exception):
    """业务异常：由 Router/Service 抛出，统一处理器转成错误响应。"""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        details: dict[str, Any] | None = None,
        http_status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}
        self.http_status = http_status or HTTP_STATUS.get(code, 400)


# --- 便捷构造 ---------------------------------------------------------------


def not_found(message: str = "资源不存在", **details: Any) -> AppError:
    return AppError(ErrorCode.RESOURCE_NOT_FOUND, message, details or None)


def perm_denied(message: str = "权限不足", **details: Any) -> AppError:
    return AppError(ErrorCode.PERM_DENIED, message, details or None)


def conflict(code: ErrorCode, message: str, **details: Any) -> AppError:
    return AppError(code, message, details or None)


def validation_error(message: str, **details: Any) -> AppError:
    return AppError(ErrorCode.VALIDATION_ERROR, message, details or None)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=exc.http_status, content=error_body(exc.code, exc.message, exc.details))

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        fields = [
            {"loc": ".".join(str(part) for part in item.get("loc", [])), "msg": item.get("msg", "")}
            for item in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content=error_body(ErrorCode.VALIDATION_ERROR, "参数校验失败", {"fields": fields}),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = ErrorCode.RESOURCE_NOT_FOUND if exc.status_code == 404 else ErrorCode.INTERNAL_ERROR
        if exc.status_code in {401, 403}:
            code = ErrorCode.AUTH_TOKEN_INVALID if exc.status_code == 401 else ErrorCode.PERM_DENIED
        return JSONResponse(status_code=exc.status_code, content=error_body(code, str(exc.detail)))

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        request_id = getattr(request.state, "request_id", None)
        logger.exception("未处理异常 request_id=%s path=%s", request_id, request.url.path)
        return JSONResponse(
            status_code=500,
            content=error_body(ErrorCode.INTERNAL_ERROR, "服务器内部错误", {"request_id": request_id}),
        )
