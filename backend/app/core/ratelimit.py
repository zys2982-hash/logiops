"""进程内滑动窗口限流（演示级，符合 ADR「不引入 Redis / MQ」的取舍）。

背景：基线文档 §10 承诺「AI 分析按用户 1 次/5 秒（429 RATE_LIMITED）」，但此前
后端只定义了错误码、没有任何实现，前端还写了对应文案——属于「幽灵功能」。
这里用进程内字典实现最小可用版本，并暴露 ``reset_rate_limits()`` 供测试隔离。

取舍（写进文档，不假装它是分布式限流）：
- 单进程有效；多副本部署时每个副本各限各的（要严格全局限流需 Redis/网关）。
- 只对"昂贵且会被手点"的 AI 分析入口生效，其余接口不限流。
"""

from __future__ import annotations

import time
from threading import Lock

_last_call_at: dict[str, float] = {}
_lock = Lock()


def check_rate_limit(key: str, *, min_interval_seconds: float, now: float | None = None) -> float:
    """放行则记录本次时间并返回 0；被限流则返回还需等待的秒数（不更新记录）。

    ``min_interval_seconds <= 0`` 表示关闭限流（测试与本地调试用）。
    """
    if min_interval_seconds <= 0:
        return 0.0
    moment = time.monotonic() if now is None else now
    with _lock:
        last = _last_call_at.get(key)
        if last is not None:
            elapsed = moment - last
            if elapsed < min_interval_seconds:
                return min_interval_seconds - elapsed
        _last_call_at[key] = moment
    return 0.0


def reset_rate_limits() -> None:
    """清空限流状态（测试/演示重置用）。"""
    with _lock:
        _last_call_at.clear()


__all__ = ["check_rate_limit", "reset_rate_limits"]
