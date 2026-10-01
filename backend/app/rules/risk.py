"""风险等级规则（基线文档 §8.6）——等级由这里决定，LLM 无权修改。"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.models.enums import CustomerLevel, ExceptionLevel, ExceptionType

MAX_SCORE = 4


@dataclass(frozen=True)
class RiskFactor:
    code: str
    label: str
    weight: int
    detail: str

    def as_dict(self) -> dict:
        return {"code": self.code, "label": self.label, "weight": self.weight, "detail": self.detail}


@dataclass(frozen=True)
class RiskResult:
    score: int
    level: str
    factors: list[RiskFactor] = field(default_factory=list)

    @property
    def factor_dicts(self) -> list[dict]:
        return [factor.as_dict() for factor in self.factors]

    @property
    def explanation(self) -> str:
        if not self.factors:
            return "无风险加分项"
        parts = [f"{factor.label}(+{factor.weight})" for factor in self.factors if factor.weight]
        return "、".join(parts) if parts else "无风险加分项"


def base_score(delay_minutes: int | None) -> int:
    """延误分钟数分档：0 / 1–120 / 121–360 / >360。"""
    minutes = delay_minutes or 0
    if minutes <= 0:
        return 0
    if minutes <= 120:
        return 1
    if minutes <= 360:
        return 2
    return 3


def level_of(score: int) -> str:
    if score <= 0:
        return ExceptionLevel.LOW
    if score <= 2:
        return ExceptionLevel.MEDIUM
    if score == 3:
        return ExceptionLevel.HIGH
    return ExceptionLevel.CRITICAL


def evaluate_risk(
    *,
    delay_minutes: int | None,
    customer_level: str | None = None,
    exception_type: str | None = None,
    sla_breached: bool = False,
    vip_upgrade: bool = True,
) -> RiskResult:
    score = base_score(delay_minutes)
    factors: list[RiskFactor] = [
        RiskFactor(
            code="DELAY_BASE",
            label="延误时长",
            weight=score,
            detail=f"预计延误 {delay_minutes or 0} 分钟",
        )
    ]

    if vip_upgrade:
        if str(customer_level) == str(CustomerLevel.SVIP):
            score += 2
            factors.append(RiskFactor("CUSTOMER_SVIP", "SVIP 客户", 2, "SVIP 客户优先级最高"))
        elif str(customer_level) == str(CustomerLevel.VIP):
            score += 1
            factors.append(RiskFactor("CUSTOMER_VIP", "VIP 客户", 1, "VIP 客户需优先处理"))

    if str(exception_type) == str(ExceptionType.VEHICLE_BREAKDOWN):
        score += 1
        factors.append(RiskFactor("VEHICLE_BREAKDOWN", "车辆故障", 1, "车辆故障通常需要外部资源介入"))

    if sla_breached:
        score += 1
        factors.append(RiskFactor("SLA_BREACH", "SLA 已违约", 1, "已超出客户承诺时间"))

    capped = min(score, MAX_SCORE)
    return RiskResult(score=capped, level=level_of(capped), factors=factors)
