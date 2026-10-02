"""Demo 控制接口（基线文档 §13.4）：推进时钟、重置演示数据、查看状态。

面试现场"翻车恢复"入口：POST /demo/actions/reset。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.deps import ContextDep, require
from app.core.clock import now_utc, parse_dt
from app.core.clock import state as clock_state
from app.core.config import get_settings
from app.core.errors import AppError, ErrorCode, validation_error
from app.core.permissions import Perm

router = APIRouter(prefix="/demo", tags=["demo"], dependencies=[Depends(require(Perm.DEMO_CONTROL))])


class TickRequest(BaseModel):
    minutes: int = Field(default=60, ge=1, le=60 * 24 * 30, description="推进的业务分钟数")


class ResetRequest(BaseModel):
    scenario: str = Field(default="case-a", description="重置后停留的场景")


class SetClockRequest(BaseModel):
    target_utc: str = Field(
        min_length=1,
        max_length=64,
        description="目标业务时间（ISO 8601，可带 Z 或时区偏移；如 2026-10-05T06:30:00Z）",
    )


def _seed_module():
    try:
        from app.seed import reset_demo_data  # type: ignore

        return reset_demo_data
    except Exception:
        return None


@router.get("/state", summary="当前演示状态")
def demo_state(ctx: ContextDep) -> dict:
    settings = get_settings()
    return {
        "base_date": clock_state.anchor().isoformat(),
        "offset_minutes": clock_state.offset_minutes,
        "now_utc": now_utc().isoformat(),
        "clock_mode": settings.clock_mode,
        "ai_mode": settings.ai_mode,
        "workspace_id": ctx.workspace_id,
        "seed_available": _seed_module() is not None,
    }


@router.post("/actions/set-clock", summary="把业务时钟直接跳到指定时刻（选年月日时分秒）")
def demo_set_clock(ctx: ContextDep, payload: SetClockRequest) -> dict:
    """演示工具的"时间跳转"：把虚拟时钟锚点设为目标时刻、偏移清零。

    与 tick 的区别：tick 是"往前走 N 分钟"，这里是"直接到某个时刻"（秒级）。
    只改时钟、**不触发**任何业务链路；要顺带跑一次检测，跳转后再点一次「快进 1 分钟」即可。
    """
    try:
        target = parse_dt(payload.target_utc)
    except Exception as exc:  # noqa: BLE001 - 任何解析失败都归为参数错误
        raise validation_error(
            "目标时间不是合法的 ISO 8601 时间",
            fields=[{"loc": "target_utc", "msg": f"无法解析「{payload.target_utc}」：{exc}"}],
        ) from exc

    if not 2020 <= target.year <= 2100:
        raise validation_error(
            "目标时间超出可接受范围（2020–2100）",
            fields=[{"loc": "target_utc", "msg": f"解析结果 {target.isoformat()} 不在 2020–2100 之间"}],
        )

    before = now_utc()
    clock_state.set_now(target)
    still = now_utc()
    try:
        from app.seed import sync_demo_clock_setting

        sync_demo_clock_setting(ctx.session, ctx.workspace_id)
    except Exception:  # pragma: no cover - seed 模块缺失时不影响跳转主流程
        pass
    ctx.audit(
        "demo.set_clock",
        resource_type="workspace",
        resource_id=ctx.workspace_id,
        before={"now_utc": before.isoformat()},
        after={"now_utc": still.isoformat(), "base_date": clock_state.anchor().isoformat()},
    )
    return {
        "ok": True,
        "previous_now_utc": before.isoformat(),
        "base_date": clock_state.anchor().isoformat(),
        "offset_minutes": clock_state.offset_minutes,
        "now_utc": still.isoformat(),
    }


@router.post("/actions/tick", summary="推进业务时钟（触发 ETA 重算/检测/自动关闭）")
def demo_tick(ctx: ContextDep, payload: TickRequest) -> dict:
    from app.services.tick import run_tick

    result = run_tick(ctx.session, ctx.repos, workspace_id=ctx.workspace_id, minutes=payload.minutes)
    # 把最新时钟偏移回写 system_setting，使库里的 demo.clock_offset_minutes 与前端/文档口径一致
    try:
        from app.seed import sync_demo_clock_setting

        sync_demo_clock_setting(ctx.session, ctx.workspace_id)
    except Exception:  # pragma: no cover - seed 模块缺失时不影响 tick 主流程
        pass
    ctx.audit("demo.tick", resource_type="workspace", resource_id=ctx.workspace_id, after={"minutes": payload.minutes})
    return {
        "offset_minutes": clock_state.offset_minutes,
        "now_utc": now_utc().isoformat(),
        **result,
    }


@router.post("/actions/reset", summary="重置演示数据（重建 seed + 时钟归零）")
def demo_reset(ctx: ContextDep, payload: ResetRequest) -> dict:
    reset = _seed_module()
    if reset is None:
        raise AppError(ErrorCode.INTERNAL_ERROR, "seed 模块尚未实现（app/seed/__init__.py）")
    clock_state.reset()
    summary = reset(ctx.session, workspace_id=ctx.workspace_id, scenario=payload.scenario)
    return {"reset": True, "scenario": payload.scenario, "summary": summary}


@router.post("/actions/advance-to-less", summary="推进到主案例送达（用于演示自动关闭）")
def demo_advance(ctx: ContextDep) -> dict:
    from app.services.tick import advance_until_delivered

    result = advance_until_delivered(ctx.session, ctx.repos, workspace_id=ctx.workspace_id)
    ctx.audit("demo.advance_to_delivered", resource_type="workspace", resource_id=ctx.workspace_id, after=result)
    return {"offset_minutes": clock_state.offset_minutes, "now_utc": now_utc().isoformat(), **result}
