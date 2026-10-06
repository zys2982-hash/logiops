"""Demo 控制接口（基线文档 §13.4）：推进时钟、重置演示数据、查看状态。

面试现场"翻车恢复"入口：POST /demo/actions/reset。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.deps import ContextDep, require
from app.core.clock import now_utc, parse_dt
from app.core.clock import state as clock_state
from app.core.config import current_clock_mode, get_settings, set_ai_mode, set_clock_mode
from app.core.errors import AppError, ErrorCode, validation_error
from app.core.permissions import Perm

router = APIRouter(prefix="/demo", tags=["demo"], dependencies=[Depends(require(Perm.DEMO_CONTROL))])


def _reject_when_real_clock(action: str) -> None:
    """演示时钟只在虚拟时钟（replay）下有意义；真实时间模式下明确拒绝，别让按钮"点了没反应"。

    项目默认口径是真实时间（CLOCK_MODE=system），但模式可**运行时切换**：
    在演示页把时钟切到「虚拟时钟」即可恢复 tick/跳转（不需要重启）。
    """
    mode = current_clock_mode()
    if mode != "replay":
        raise AppError(
            ErrorCode.DEMO_CLOCK_DISABLED,
            f"当前是真实时间模式（CLOCK_MODE={mode}），{action}已停用：系统时间就是现实时间，不需要推进",
            {"clock_mode": mode, "hint": "在演示页把时钟切到「虚拟时钟」，或把 CLOCK_MODE 改成 replay 并重启后端"},
        )


class TickRequest(BaseModel):
    minutes: int = Field(default=60, ge=1, le=60 * 24 * 30, description="推进的业务分钟数")


class ResetRequest(BaseModel):
    scenario: str = Field(default="case-a", description="重置后停留的场景")
    scale: str | None = Field(
        default=None,
        description="数据规模：compact（默认，每种异常类型一单）| full（1000 单完整规模）",
    )


class SetClockRequest(BaseModel):
    target_utc: str = Field(
        min_length=1,
        max_length=64,
        description="目标业务时间（ISO 8601，可带 Z 或时区偏移；如 2026-10-05T06:30:00Z）",
    )


class SetAiModeRequest(BaseModel):
    ai_mode: str = Field(
        min_length=2,
        max_length=16,
        description="AI 模式：replay（读预录样本，可复现、0 成本）或 live（真实调用大模型）",
    )


class SetClockModeRequest(BaseModel):
    clock_mode: str = Field(
        min_length=4,
        max_length=16,
        description="时钟模式：system（真实时间，默认）或 replay（虚拟时钟：固定基准日 + tick/跳转推进）",
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
        "clock_mode": current_clock_mode(),
        "ai_mode": settings.ai_mode,
        "ai_enabled": settings.ai_enabled,
        "workspace_id": ctx.workspace_id,
        "seed_available": _seed_module() is not None,
    }


@router.post("/actions/set-clock-mode", summary="切换时钟模式（真实时间 / 虚拟时钟），立即生效")
def demo_set_clock_mode(ctx: ContextDep, payload: SetClockModeRequest) -> dict:
    """运行时切换时钟模式，不需要重启后端（与「切换 AI 模式」同一套机制）。

    - 切到 ``replay``：进程内虚拟时钟重置回固定基准日（offset 0），此后 tick / 时间跳转可用，
      新增与修改的数据时间戳也按虚拟时间写（演示可复现）；
    - 切到 ``system``：清掉偏移，系统直接用现实时间（``now()``）；
    - 只作用于**当前进程**，重启后回到 ``.env`` 的 ``CLOCK_MODE``。
    """
    before = current_clock_mode()
    try:
        mode = set_clock_mode(payload.clock_mode)
    except ValueError as exc:
        raise validation_error(
            "时钟模式非法",
            fields=[{"loc": "clock_mode", "msg": str(exc)}],
        ) from exc

    # 切换时刻把虚拟时钟归位：replay 从固定基准日重新开始；system 下偏移无意义，一并清零
    clock_state.reset()
    try:
        from app.seed import sync_demo_clock_setting

        sync_demo_clock_setting(ctx.session, ctx.workspace_id)
    except Exception:  # pragma: no cover - seed 模块缺失时不影响切换主流程
        pass

    still = now_utc()
    warning = None
    if mode == "replay":
        warning = (
            "已切到虚拟时钟：业务时间回到固定基准日、需要点「快进」才往前走；"
            "此后新增/修改的数据时间戳也按虚拟时间写"
        )
    ctx.audit(
        "demo.set_clock_mode",
        resource_type="workspace",
        resource_id=ctx.workspace_id,
        before={"clock_mode": before},
        after={"clock_mode": mode, "now_utc": still.isoformat()},
    )
    return {
        "ok": True,
        "clock_mode": mode,
        "previous_clock_mode": before,
        "runtime_only": True,
        "now_utc": still.isoformat(),
        "base_date": clock_state.anchor().isoformat(),
        "offset_minutes": clock_state.offset_minutes,
        "warning": warning,
    }


class SetOrderStatusRequest(BaseModel):
    order_id: int = Field(ge=1, description="订单 id")
    status: str = Field(
        min_length=4,
        max_length=16,
        description="目标状态：CREATED / DISPATCHED / IN_TRANSIT / DELIVERED / CLOSED / CANCELLED",
    )
    note: str | None = Field(default=None, max_length=255, description="留痕说明（写进审计）")


class SetExceptionStatusRequest(BaseModel):
    exception_id: int = Field(ge=1, description="异常单 id")
    status: str = Field(
        min_length=4,
        max_length=16,
        description="目标状态：DETECTED（待确认）/ PROCESSING（处理中）/ RESOLVED（已解决）/ CLOSED（已关闭）",
    )
    note: str | None = Field(default=None, max_length=255, description="留痕说明（写进事件与审计）")


@router.post(
    "/actions/set-order-status",
    summary="演示：直接设定订单状态（跳过状态机，写审计；用户口径「状态要能自己选」）",
)
def demo_set_order_status(ctx: ContextDep, payload: SetOrderStatusRequest) -> dict:
    """演示/管理用途：把订单状态直接设成任意目标状态。

    不做状态机合法性拦截（允许回退），但字段自洽（补/清 dispatched_at、delivered_at）并写
    ``order.status_forced`` 审计。不级联改异常单状态 —— 订单与异常可以分别直设。
    """
    from app.services.orders import OrderService

    before_status = str(ctx.repos.orders.get_or_404(payload.order_id, "订单不存在").status)
    order = OrderService(ctx.repos).force_status(
        payload.order_id, payload.status, note=payload.note, actor_id=ctx.user.id
    )
    return {
        "ok": True,
        "order_id": order.id,
        "order_no": order.order_no,
        "previous_status": before_status,
        "status": str(order.status),
        "forced": True,
        "note": payload.note,
    }


@router.post(
    "/actions/set-exception-status",
    summary="演示：直接设定异常状态（跳过状态机，写事件 + 审计）",
)
def demo_set_exception_status(ctx: ContextDep, payload: SetExceptionStatusRequest) -> dict:
    """演示/管理用途：把异常状态直接设成任意目标状态。

    进入 RESOLVED/CLOSED 会释放车辆维修状态并补结束时间；回到进行中状态会清掉结束时间；
    每次写一条 STATUS_CHANGED 事件（detail.forced=true）与 ``exception.status_forced`` 审计。
    """
    from app.services.exceptions import ExceptionService

    service = ExceptionService(ctx.repos)
    before_status = str(service.get(payload.exception_id).status)
    case = service.force_status(
        payload.exception_id, payload.status, note=payload.note, actor_id=ctx.user.id
    )
    return {
        "ok": True,
        "exception_id": case.id,
        "case_no": case.case_no,
        "previous_status": before_status,
        "status": str(case.status),
        "forced": True,
        "note": payload.note,
    }


@router.post("/actions/set-ai-mode", summary="切换 AI 模式（回放样本 / 真实大模型），立即生效")
def demo_set_ai_mode(ctx: ContextDep, payload: SetAiModeRequest) -> dict:
    """运行时切换 AI 模式，不需要重启后端。

    说明：切换只作用于**当前进程**（`get_settings()` 单例），也就是下一个分析就生效；
    重启后回到 ``.env`` 的 ``AI_MODE``（故意不写库，避免"库里 live、实际 replay"的错觉）。
    """
    settings = get_settings()
    before = settings.ai_mode
    try:
        mode = set_ai_mode(payload.ai_mode)
    except ValueError as exc:
        raise validation_error(
            "AI 模式非法",
            fields=[{"loc": "ai_mode", "msg": str(exc)}],
        ) from exc

    warning = None
    if mode == "live" and not settings.llm_api_key:
        warning = "已切到 live，但未配置 LLM_API_KEY：分析会失败并降级为确定性模板，请先在后端 .env 填写 key 并重启"

    ctx.audit(
        "demo.set_ai_mode",
        resource_type="workspace",
        resource_id=ctx.workspace_id,
        before={"ai_mode": before},
        after={"ai_mode": mode},
    )
    return {
        "ok": True,
        "ai_mode": mode,
        "previous_ai_mode": before,
        "runtime_only": True,
        "warning": warning,
    }


@router.post("/actions/set-clock", summary="把业务时钟直接跳到指定时刻（选年月日时分秒）")
def demo_set_clock(ctx: ContextDep, payload: SetClockRequest) -> dict:
    """演示工具的"时间跳转"：把虚拟时钟锚点设为目标时刻、偏移清零。

    与 tick 的区别：tick 是"往前走 N 分钟"，这里是"直接到某个时刻"（秒级）。
    只改时钟、**不触发**任何业务链路；要顺带跑一次检测，跳转后再点一次「快进 1 分钟」即可。
    """
    _reject_when_real_clock("时间跳转（set-clock）")
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


@router.post("/actions/tick", summary="推进业务时钟（仅 CLOCK_MODE=replay；真实时间模式下拒绝）")
def demo_tick(ctx: ContextDep, payload: TickRequest) -> dict:
    from app.services.tick import run_tick

    _reject_when_real_clock("推进业务时钟（tick）")
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
    summary = reset(ctx.session, workspace_id=ctx.workspace_id, scenario=payload.scenario, scale=payload.scale)
    return {"reset": True, "scenario": payload.scenario, "scale": summary.get("seed_scale"), "summary": summary}


@router.post("/actions/advance-to-less", summary="推进到主案例送达并显式收口目标异常（仅 CLOCK_MODE=replay）")
def demo_advance(ctx: ContextDep) -> dict:
    """演示页「推进到送达并关闭」：推进时钟到送达，然后**以人工起点**关掉目标异常。

    2026-10-06 起异常不再随送达 / 超时自动收口；这里是按钮显式要求的一次性收口
    （close_target=True，审计 actor = 当前用户），不是隐式自动关闭。
    """
    from app.services.tick import advance_until_delivered

    _reject_when_real_clock("推进到送达（advance-to-less，依赖虚拟时钟跳跃）")
    result = advance_until_delivered(
        ctx.session,
        ctx.repos,
        workspace_id=ctx.workspace_id,
        close_target=True,
        actor_id=ctx.user.id,
    )
    ctx.audit("demo.advance_to_delivered", resource_type="workspace", resource_id=ctx.workspace_id, after=result)
    return {"offset_minutes": clock_state.offset_minutes, "now_utc": now_utc().isoformat(), **result}
