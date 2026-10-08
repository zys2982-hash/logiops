"""时钟抽象（基线文档 §13.1）。

全系统不直接调用 datetime.now()，一律走 get_clock().now()。
- SystemClock：真实时间
- ReplayClock：DEMO_BASE_DATE + 偏移量，偏移由 /demo/actions/tick 推进
这样 Demo 与测试完全确定性、可复现。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.core.config import get_settings

UTC = UTC

try:  # Windows 上若未安装 tzdata，退化为固定 +08:00（本项目只用这一个时区）
    LOCAL_TZ: timezone | ZoneInfo = ZoneInfo("Asia/Shanghai")
except Exception:  # pragma: no cover - 取决于运行环境
    LOCAL_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")


def parse_dt(value: str | datetime) -> datetime:
    """解析为 aware UTC datetime。"""
    if isinstance(value, datetime):
        dt = value
    else:
        text = value.strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=LOCAL_TZ)
    return dt.astimezone(UTC)


def ensure_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def to_local(dt: datetime | None) -> datetime | None:
    dt = ensure_utc(dt)
    return None if dt is None else dt.astimezone(LOCAL_TZ)


def local_text(dt: datetime | None, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    """把库里的朴素 UTC 时间渲染成业务时区（Asia/Shanghai）文本，供「人可读摘要」使用。

    为什么需要它：有些字段（如 exception_case.impact_summary）是**在服务端拼成整串再落库**的，
    前端拿到的已经是纯文本、没有时间字段可以转换。若这里直接插 datetime 对象（默认 str()），
    存进去的就是 UTC —— 界面上会表现为「页头北京时间、摘要 UTC」的同屏不一致。
    用户口径（2026-10-08）：**全部统一北京时间**。

    注意：DB 里是朴素 UTC。绝不能用 naive.astimezone(LOCAL_TZ) —— 那会按**本机时区**解释，
    在东八区机器上等于没转换（本机实测：08:09 直接 astimezone 仍是 08:09，走本函数才是 16:09）。
    """
    local = to_local(dt)
    return "-" if local is None else local.strftime(fmt)


class ClockState:
    """ReplayClock 的可变状态（进程内单例；Demo/reset 会重置它）。"""

    def __init__(self) -> None:
        self.base: datetime | None = None
        self.offset_minutes: int = 0

    def configure(self, base: datetime, offset_minutes: int = 0) -> None:
        self.base = ensure_utc(base)
        self.offset_minutes = offset_minutes

    def reset(self) -> None:
        self.base = None
        self.offset_minutes = 0

    def now(self) -> datetime:
        if self.base is None:
            return default_base_date() + timedelta(minutes=self.offset_minutes)
        return self.base + timedelta(minutes=self.offset_minutes)

    def advance(self, minutes: int) -> datetime:
        self.offset_minutes += int(minutes)
        return self.now()

    def set_now(self, target: datetime) -> datetime:
        """把业务时钟**直接跳到**指定时刻（演示工具的"时间跳转"，秒级精确）。

        做法：把锚点 ``base`` 设为目标、偏移清零，于是 ``now()`` 精确等于目标；
        之后 ``advance(minutes)`` 仍在此基础上继续推进。
        """
        self.base = ensure_utc(target)
        self.offset_minutes = 0
        return self.now()

    def anchor(self) -> datetime:
        """当前时钟锚点：跳转过就是跳转目标，否则是配置的业务基准日。"""
        return self.base or default_base_date()


state = ClockState()


def default_base_date() -> datetime:
    return parse_dt(get_settings().demo_base_date)


class SystemClock:
    mode = "system"

    def now(self) -> datetime:
        return datetime.now(UTC)


class ReplayClock:
    mode = "replay"

    def now(self) -> datetime:
        return state.now()


def get_clock() -> SystemClock | ReplayClock:
    if get_settings().clock_mode.lower() == "system":
        return SystemClock()
    return ReplayClock()


def now_utc() -> datetime:
    return get_clock().now()


def utcnow_naive() -> datetime:
    """ORM 列默认值用：MySQL/SQLite 的 DATETIME 不存时区，统一写"朴素 UTC"。"""
    return get_clock().now().replace(tzinfo=None)


def today_local() -> datetime:
    """本地（Asia/Shanghai）当天 00:00，用于 Dashboard 今日统计。"""
    return to_local(now_utc()).replace(hour=0, minute=0, second=0, microsecond=0)
