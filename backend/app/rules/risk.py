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


def vehicle_breakdown_factor() -> RiskFactor:
    """「车辆故障」因子（单一出处）。

    读取时自愈（eta_flow.sync_case_vehicle_factor）需要按现状加/减这一个因子，
    从这里取，保证与 evaluate_risk 里生成的 label/weight/detail 永远一致。
    """
    return RiskFactor("VEHICLE_BREAKDOWN", "车辆故障", 1, "车辆故障通常需要外部资源介入")


def evaluate_risk(
    *,
    delay_minutes: int | None = None,
    customer_level: str | None = None,
    exception_type: str | None = None,
    vip_upgrade: bool = True,
    vehicle_repairing: bool | None = None,
) -> RiskResult:
    """风险分 = **这一个异常本身**的权重 + 客户等级放大（用户口径 2026-10-05）。

    用户原话：「一个异常订单的逻辑应该只受到一个异常的影响，比如我在某个订单里创建了车辆异常，
    并且这个客户是 vip 客户，那么风险等级就是车辆异常的 1 加上 vip 的 1 等于 2」。

    所以按"问题类型"分流，两种问题**互不叠加**：
    · `VEHICLE_BREAKDOWN`（车辆故障）：车辆故障 1 + 客户等级（VIP 1 / SVIP 2）—— 不计延误、不计违约；
    · `DELAY_RISK`（延误，送达后按实际时间产生）：延误档位 1/2/3 + 客户等级 —— 建单前提就是
      "实际送达已超允许延迟"，不再重复加"违约 1"（旧模型会，导致同一事实计两次）；
    · `type` 未指定（历史调用）：按延误口径算，保持兼容。
    客户等级是"放大项"，不算另一个异常。分数封顶 4。
    """
    score = 0
    factors: list[RiskFactor] = []
    kind = str(exception_type) if exception_type is not None else str(ExceptionType.DELAY_RISK)

    if kind == str(ExceptionType.DELAY_RISK):
        base = base_score(delay_minutes)
        score += base
        factors.append(
            RiskFactor(
                code="DELAY_BASE",
                label="延误时长",
                weight=base,
                detail=f"实际延误 {delay_minutes or 0} 分钟",
            )
        )
    # 车辆故障因子按**现状**计：None = 只看类型；False = 车辆已恢复，不再计入（显示当前风险）
    if kind == str(ExceptionType.VEHICLE_BREAKDOWN) and vehicle_repairing is not False:
        score += 1
        factors.append(vehicle_breakdown_factor())

    if vip_upgrade:
        if str(customer_level) == str(CustomerLevel.SVIP):
            score += 2
            factors.append(RiskFactor("CUSTOMER_SVIP", "SVIP 客户", 2, "SVIP 客户优先级最高"))
        elif str(customer_level) == str(CustomerLevel.VIP):
            score += 1
            factors.append(RiskFactor("CUSTOMER_VIP", "VIP 客户", 1, "VIP 客户需优先处理"))

    capped = min(score, MAX_SCORE)
    return RiskResult(score=capped, level=level_of(capped), factors=factors)
