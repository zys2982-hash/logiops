"""ETA 重算规则（基线文档 §8.5）——可解释，不用 ML。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

DEFAULT_SPEED_KMH = 40.0
DEFAULT_PROGRESS_RATIO = 0.6
SPEED_WINDOW_HOURS = 2


class EtaMethod:
    REPAIR_WAIT = "REPAIR_WAIT"
    MOVING_AVG_SPEED = "MOVING_AVG_SPEED"
    FALLBACK = "FALLBACK"


@dataclass(frozen=True)
class EtaResult:
    eta_at: datetime
    method: str
    remaining_km: float
    avg_speed_kmh: float
    resume_at: datetime
    detail: str

    def as_dict(self) -> dict:
        return {
            "eta_at": self.eta_at.isoformat(),
            "method": self.method,
            "remaining_km": round(self.remaining_km, 1),
            "avg_speed_kmh": round(self.avg_speed_kmh, 1),
            "resume_at": self.resume_at.isoformat(),
            "detail": self.detail,
        }


def _naive(dt: datetime) -> datetime:
    return dt.replace(tzinfo=None) if dt.tzinfo is not None else dt


def estimate_remaining_km(distance_km: int | float | None, progress_ratio: float | None = None) -> float:
    total = float(distance_km or 0.0)
    ratio = DEFAULT_PROGRESS_RATIO if progress_ratio is None else min(max(float(progress_ratio), 0.0), 1.0)
    return max(total * (1.0 - ratio), 0.0)


def avg_speed_from_events(
    events: Sequence[Any], *, now: datetime, window_hours: int = SPEED_WINDOW_HOURS
) -> float | None:
    """用最近窗口内的速度样本求平均（按发生时间倒序或正序都可）。"""
    now = _naive(now)
    samples: list[float] = []
    for event in events:
        occurred = getattr(event, "occurred_at", None)
        speed = getattr(event, "speed_kmh", None)
        if occurred is None or speed is None:
            continue
        occurred_n = _naive(occurred)
        if now - timedelta(hours=window_hours) <= occurred_n <= now:
            value = float(speed)
            if value > 0:
                samples.append(value)
    if not samples:
        return None
    return sum(samples) / len(samples)


def recalc(
    *,
    now: datetime,
    distance_km: int | float | None,
    progress_ratio: float | None = None,
    repair_recovery_at: datetime | None = None,
    events: Sequence[Any] = (),
    default_speed_kmh: float = DEFAULT_SPEED_KMH,
) -> EtaResult:
    now_n = _naive(now)
    remaining_km = estimate_remaining_km(distance_km, progress_ratio)

    speed_measured = avg_speed_from_events(events, now=now_n)
    if repair_recovery_at is not None:
        resume_at = max(_naive(repair_recovery_at), now_n)
        method = EtaMethod.REPAIR_WAIT
        detail = "按承运商给出的恢复时间起算"
    else:
        resume_at = now_n
        method = EtaMethod.MOVING_AVG_SPEED if speed_measured else EtaMethod.FALLBACK
        detail = "按最近 2 小时均速估算" if speed_measured else f"无速度样本，按默认 {default_speed_kmh} km/h 估算"

    speed = float(speed_measured or default_speed_kmh)
    if speed <= 0:
        speed = default_speed_kmh
    travel_hours = remaining_km / speed
    eta_at = resume_at + timedelta(hours=travel_hours)
    return EtaResult(
        eta_at=eta_at,
        method=method,
        remaining_km=remaining_km,
        avg_speed_kmh=speed,
        resume_at=resume_at,
        detail=detail,
    )
