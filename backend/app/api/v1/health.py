"""健康检查与运行态信息。"""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import text

from app.core.clock import now_utc, state
from app.core.config import current_clock_mode, get_settings
from app.db.session import get_engine
from app.models import TABLES

router = APIRouter(tags=["system"])


@router.get("/healthz", summary="健康检查")
def healthz() -> dict:
    settings = get_settings()
    db_ok = True
    db_error: str | None = None
    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - 依赖真实数据库
        db_ok = False
        db_error = f"{type(exc).__name__}: {str(exc)[:160]}"

    return {
        "status": "ok" if db_ok else "degraded",
        "db": db_ok,
        "db_error": db_error,
        "app_env": settings.app_env,
        "clock_mode": current_clock_mode(),
        "ai_mode": settings.ai_mode,
        "demo_base_date": settings.demo_base_date,
        "clock_offset_minutes": state.offset_minutes,
        "now_utc": now_utc().isoformat(),
        "model_tables": len(TABLES),
    }


@router.get("/meta/enums", summary="枚举字典（前端下拉用）")
def meta_enums() -> dict:
    from app.models import enums

    names = [
        "Role",
        "CustomerLevel",
        "CarrierStatus",
        "VehicleStatus",
        "DriverStatus",
        "OrderStatus",
        "TrackingEventType",
        "SlaScopeType",
        "ExceptionType",
        "ExceptionLevel",
        "ExceptionStatus",
        "ExceptionEventType",
        "MessageChannel",
        "ParseStatus",
        "AnalysisStatus",
        "ApprovalAction",
        "ApprovalStatus",
        "FollowupStatus",
        "NotificationChannel",
        "NotificationStatus",
        "DetectionRule",
    ]
    return {name: [member.value for member in getattr(enums, name)] for name in names}
