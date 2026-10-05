"""异常检测规则（基线文档 §8.4）。纯决策函数：给事实，返回该不该建单、用哪条规则。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.models.enums import DetectionRule, ExceptionType, OrderStatus


@dataclass(frozen=True)
class DetectionDecision:
    rule: str | None
    exception_type: str | None
    stall_minutes: int
    reason: str
    should_create: bool
    merge_only: bool = False

    def as_dict(self) -> dict:
        return {
            "rule": self.rule,
            "exception_type": self.exception_type,
            "stall_minutes": self.stall_minutes,
            "reason": self.reason,
            "should_create": self.should_create,
            "merge_only": self.merge_only,
        }


def _naive(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=None) if dt.tzinfo is not None else dt


def stall_minutes_of(*, now: datetime, last_move_at: datetime | None) -> int:
    last_move = _naive(last_move_at)
    if last_move is None:
        return 0
    delta = _naive(now) - last_move
    return max(int(delta.total_seconds() // 60), 0)


def decide(
    *,
    now: datetime,
    order_status: str,
    last_move_at: datetime | None,
    stall_threshold_minutes: int,
    has_open_exception: bool = False,
) -> DetectionDecision:
    """检测规则（用户口径 2026-10-05 后只剩一条）。

    只保留 **停滞 ≥ 阈值 → 疑似车辆故障** 这一条：
    · 延误异常**不在在途阶段产生**（旧的 `ETA_BREACH_SLA` 按预测 ETA 建单已移除）——
      延误只在订单送达时用 `实际送达 − 承诺送达` 与 SLA 规则比对，超了才自动建单
      （见 `OrderService.mark_delivered`）；
    · 因此这里也不再需要 ETA / 承诺 / 允许延迟这些入参。
    """
    stall = stall_minutes_of(now=now, last_move_at=last_move_at)

    if str(order_status) != str(OrderStatus.IN_TRANSIT):
        return DetectionDecision(
            rule=None,
            exception_type=None,
            stall_minutes=stall,
            reason=f"订单状态为 {order_status}，不参与异常检测",
            should_create=False,
        )

    if stall >= stall_threshold_minutes:
        return DetectionDecision(
            rule=str(DetectionRule.STALL_OVER_THRESHOLD),
            exception_type=str(ExceptionType.VEHICLE_BREAKDOWN),
            stall_minutes=stall,
            reason=f"轨迹停滞 {stall} 分钟 ≥ 阈值 {stall_threshold_minutes} 分钟，疑似车辆故障",
            should_create=not has_open_exception,
            merge_only=has_open_exception,
        )

    return DetectionDecision(
        rule=None,
        exception_type=None,
        stall_minutes=stall,
        reason="未命中检测规则（在途只检测停滞；延误在送达时按实际时间判定）",
        should_create=False,
    )


def is_debounced(*, now: datetime, last_detected_at: datetime | None, debounce_minutes: int) -> bool:
    """同规则去抖：窗口内不重复建单（调用方决定是否合并刷新）。"""
    last = _naive(last_detected_at)
    if last is None:
        return False
    return _naive(now) - last < timedelta(minutes=debounce_minutes)
