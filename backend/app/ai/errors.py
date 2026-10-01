"""AI 受限层的可识别异常（基线文档 §11.8）。

设计：全部继承 `AppError`，因此调用方既可以 `except AppError` 统一处理，
也可以精确 `except AiUnavailable / AiOutputInvalid` 区分降级路径。
error_code 与 §10.2 错误码表一致：LLM_UNAVAILABLE(503) / AI_OUTPUT_INVALID(502)。
"""

from __future__ import annotations

from app.core.errors import AppError, ErrorCode


class AiError(AppError):
    """AI 层异常基类。"""


class AiUnavailable(AiError):
    """LLM 不可用：无 Key / 超时 / 5xx / token 或成本超限 → 降级 FAILED(LLM_UNAVAILABLE)。"""

    def __init__(self, message: str = "AI 服务暂不可用", details: dict | None = None) -> None:
        super().__init__(ErrorCode.LLM_UNAVAILABLE, message, details)


class AiOutputInvalid(AiError):
    """LLM 输出非法或与系统事实不一致（修复重试后仍失败）→ FAILED(AI_OUTPUT_INVALID)。"""

    def __init__(self, message: str = "AI 输出不合法", details: dict | None = None) -> None:
        super().__init__(ErrorCode.AI_OUTPUT_INVALID, message, details)


class AiPromptError(AiError):
    """Prompt 模板变量与代码传入不一致（漏传/多传）——开发期错误，不应在生产出现。"""

    def __init__(self, message: str, details: dict | None = None) -> None:
        super().__init__(ErrorCode.INTERNAL_ERROR, message, details, http_status=500)


__all__ = ["AiError", "AiOutputInvalid", "AiPromptError", "AiUnavailable"]
