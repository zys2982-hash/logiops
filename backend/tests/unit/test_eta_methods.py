"""ETA 三种口径（§8.5）：REPAIR_WAIT / MOVING_AVG_SPEED / FALLBACK，并落库到 order.current_eta_at。"""

from __future__ import annotations

from datetime import datetime, timedelta

from app.models.enums import OrderStatus
from app.rules import eta
from app.services import eta_flow
from tests.unit import _support

NOW = datetime(2026, 9, 30, 11, 0)


def test_repair_wait_uses_recovery_time():
    result = eta.recalc(
        now=NOW,
        distance_km=400,
        progress_ratio=0.5,
        repair_recovery_at=NOW + timedelta(hours=1),
    )
    assert result.method == eta.EtaMethod.REPAIR_WAIT
    assert result.remaining_km == 200
    assert result.resume_at == NOW + timedelta(hours=1)
    assert result.eta_at > result.resume_at


def test_moving_avg_speed_from_recent_events():
    events = [
        type("E", (), {"occurred_at": NOW - timedelta(minutes=30), "speed_kmh": 60})(),
        type("E", (), {"occurred_at": NOW - timedelta(minutes=90), "speed_kmh": 40})(),
        type("E", (), {"occurred_at": NOW - timedelta(hours=5), "speed_kmh": 10})(),  # 窗口外
    ]
    result = eta.recalc(now=NOW, distance_km=200, progress_ratio=0.5, events=events)
    assert result.method == eta.EtaMethod.MOVING_AVG_SPEED
    assert result.avg_speed_kmh == 50


def test_fallback_speed_when_no_samples():
    result = eta.recalc(now=NOW, distance_km=200, progress_ratio=0.5)
    assert result.method == eta.EtaMethod.FALLBACK
    assert result.avg_speed_kmh == eta.DEFAULT_SPEED_KMH
    assert result.remaining_km == 100


def test_default_progress_ratio_used_when_unknown():
    result = eta.recalc(now=NOW, distance_km=1000)
    assert result.remaining_km == 1000 * (1 - eta.DEFAULT_PROGRESS_RATIO)


def test_recovery_before_now_falls_back_to_now():
    result = eta.recalc(
        now=NOW, distance_km=100, progress_ratio=0.5, repair_recovery_at=NOW - timedelta(hours=3)
    )
    assert result.method == eta.EtaMethod.REPAIR_WAIT
    assert result.resume_at == NOW


def test_service_writes_current_eta_and_records_method(db_session, bootstrap):
    repos = _support.repos_for(db_session, bootstrap)
    order = _support.in_transit(repos, bootstrap, speed_kmh=50)
    event = repos.tracking.latest(order.id)
    assert event is not None
    assert event.payload_json["_eta"]["method"] in {
        eta.EtaMethod.MOVING_AVG_SPEED,
        eta.EtaMethod.FALLBACK,
    }
    assert repos.orders.get(order.id).current_eta_at is not None

    repairs = _support.track(repos, order, event_type="REPAIR_START", city="济南")
    assert repairs.event_type == "REPAIR_START"
    assert bootstrap["vehicle"].status == "REPAIRING"

    loaded = repos.orders.get(order.id)
    result = eta_flow.recalc_order_eta(
        repos, loaded, repair_recovery_at=bootstrap["base_time"] + timedelta(hours=2)
    )
    assert result.method == eta.EtaMethod.REPAIR_WAIT
    assert loaded.status == str(OrderStatus.IN_TRANSIT)
    assert loaded.current_eta_at == result.eta_at


def test_progress_ratio_reads_payload_first(db_session, bootstrap):
    repos = _support.repos_for(db_session, bootstrap)
    order = _support.in_transit(repos, bootstrap)
    _support.track(repos, order, event_type="NOTE", city="济南", payload={"progress_ratio": 0.9})
    assert eta_flow.progress_ratio(repos, repos.orders.get(order.id)) == 0.9
