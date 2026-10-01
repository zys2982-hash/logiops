"""Demo 控制接口（基线文档 §13.4）：推进时钟、重置演示数据、查看状态。

面试现场"翻车恢复"入口：POST /demo/actions/reset。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.deps import ContextDep, require
from app.core.clock import default_base_date, now_utc
from app.core.clock import state as clock_state
from app.core.config import get_settings
from app.core.errors import AppError, ErrorCode
from app.core.permissions import Perm

router = APIRouter(prefix="/demo", tags=["demo"], dependencies=[Depends(require(Perm.DEMO_CONTROL))])


class TickRequest(BaseModel):
    minutes: int = Field(default=60, ge=1, le=60 * 24 * 30, description="推进的业务分钟数")


class ResetRequest(BaseModel):
    scenario: str = Field(default="case-a", description="重置后停留的场景")


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
        "base_date": default_base_date().isoformat(),
        "offset_minutes": clock_state.offset_minutes,
        "now_utc": now_utc().isoformat(),
        "clock_mode": settings.clock_mode,
        "ai_mode": settings.ai_mode,
        "workspace_id": ctx.workspace_id,
        "seed_available": _seed_module() is not None,
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
