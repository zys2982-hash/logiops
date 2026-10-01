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
    expected_eta_at: datetime | None = None,
    promised_delivery_at: datetime | None = None,
    max_delay_minutes: int = 0,
    has_open_exception: bool = False,
) -> DetectionDecision:
    """检测优先级：停滞 > ETA 违约 > 不建单。"""
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

    expected = _naive(expected_eta_at)
    promised = _naive(promised_delivery_at)
    if expected is not None and promised is not None:
        delay_minutes = int(round((expected - promised).total_seconds() / 60))
        if delay_minutes > max_delay_minutes:
            return DetectionDecision(
                rule=str(DetectionRule.ETA_BREACH_SLA),
                exception_type=str(ExceptionType.DELAY_RISK),
                stall_minutes=stall,
                reason=f"重算 ETA 将违约：预计延误 {delay_minutes} 分钟 > 允许 {max_delay_minutes} 分钟",
                should_create=not has_open_exception,
                merge_only=has_open_exception,
            )

    return DetectionDecision(
        rule=None,
        exception_type=None,
        stall_minutes=stall,
        reason="未命中任何检测规则",
        should_create=False,
    )


def is_debounced(*, now: datetime, last_detected_at: datetime | None, debounce_minutes: int) -> bool:
    """同规则去抖：窗口内不重复建单（调用方决定是否合并刷新）。"""
    last = _naive(last_detected_at)
    if last is None:
        return False
    return _naive(now) - last < timedelta(minutes=debounce_minutes)
