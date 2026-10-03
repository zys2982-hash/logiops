"""状态机：所有合法流转必须成功、所有非法流转必须 409 STATE_TRANSITION_INVALID（§8.1 / §8.2）。"""

from __future__ import annotations

import pytest

from app.core.errors import AppError, ErrorCode
from app.models.enums import ExceptionStatus, OrderStatus
from app.rules import state_machine
from app.services.common import apply_transition, check_version
from app.services.orders import OrderService

ORDER_LEGAL = [
    (OrderStatus.CREATED, OrderStatus.DISPATCHED),
    (OrderStatus.CREATED, OrderStatus.CANCELLED),
    (OrderStatus.DISPATCHED, OrderStatus.IN_TRANSIT),
    (OrderStatus.DISPATCHED, OrderStatus.CANCELLED),
    (OrderStatus.IN_TRANSIT, OrderStatus.DELIVERED),
    (OrderStatus.DELIVERED, OrderStatus.CLOSED),
]

EXCEPTION_LEGAL = [
    (ExceptionStatus.DETECTED, ExceptionStatus.CONFIRMING),
    (ExceptionStatus.DETECTED, ExceptionStatus.CLOSED),
    (ExceptionStatus.CONFIRMING, ExceptionStatus.ANALYZING),
    (ExceptionStatus.CONFIRMING, ExceptionStatus.CLOSED),
    (ExceptionStatus.ANALYZING, ExceptionStatus.PROCESSING),
    (ExceptionStatus.ANALYZING, ExceptionStatus.CONFIRMING),
    (ExceptionStatus.ANALYZING, ExceptionStatus.CLOSED),
    (ExceptionStatus.PROCESSING, ExceptionStatus.RESOLVED),
    (ExceptionStatus.PROCESSING, ExceptionStatus.CLOSED),
    (ExceptionStatus.PROCESSING, ExceptionStatus.ANALYZING),  # 重新分析
    (ExceptionStatus.RESOLVED, ExceptionStatus.CLOSED),
]


@pytest.mark.parametrize(("current", "target"), ORDER_LEGAL)
def test_order_legal_transitions(current, target):
    plan = state_machine.plan_transition("ORDER", current, target, "TEST")
    assert plan.from_status == str(current)
    assert plan.to_status == str(target)


@pytest.mark.parametrize(("current", "target"), EXCEPTION_LEGAL)
def test_exception_legal_transitions(current, target):
    plan = state_machine.plan_transition("EXCEPTION", current, target, "TEST")
    assert plan.to_status == str(target)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (source, target)
        for source in OrderStatus
        for target in OrderStatus
        if (source, target) not in ORDER_LEGAL
    ],
)
def test_order_illegal_transitions_raise_409(current, target):
    with pytest.raises(AppError) as excinfo:
        state_machine.plan_transition("ORDER", current, target)
    assert excinfo.value.code == ErrorCode.STATE_TRANSITION_INVALID
    assert excinfo.value.http_status == 409


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (source, target)
        for source in ExceptionStatus
        for target in ExceptionStatus
        if (source, target) not in EXCEPTION_LEGAL
    ],
)
def test_exception_illegal_transitions_raise_409(current, target):
    with pytest.raises(AppError) as excinfo:
        state_machine.plan_transition("EXCEPTION", current, target)
    assert excinfo.value.code == ErrorCode.STATE_TRANSITION_INVALID


def test_terminal_statuses_are_dead_ends():
    assert state_machine.allowed_targets("ORDER", OrderStatus.CLOSED) == set()
    assert state_machine.allowed_targets("ORDER", OrderStatus.CANCELLED) == set()
    assert state_machine.allowed_targets("EXCEPTION", ExceptionStatus.CLOSED) == set()
    assert state_machine.is_terminal("EXCEPTION", ExceptionStatus.CLOSED) is True


def test_close_and_cancel_require_reason():
    with pytest.raises(AppError) as excinfo:
        state_machine.plan_transition("EXCEPTION", ExceptionStatus.DETECTED, ExceptionStatus.CLOSED)
    assert excinfo.value.code == ErrorCode.STATE_TRANSITION_INVALID
    assert state_machine.plan_transition("EXCEPTION", ExceptionStatus.DETECTED, ExceptionStatus.CLOSED, "INVALID")


def test_apply_transition_helper_is_the_only_writer():
    class Holder:
        status = str(OrderStatus.CREATED)

    holder = Holder()
    apply_transition(holder, "ORDER", OrderStatus.DISPATCHED)
    assert holder.status == str(OrderStatus.DISPATCHED)
    with pytest.raises(AppError):
        apply_transition(holder, "ORDER", OrderStatus.DELIVERED)


def test_dispatch_twice_conflicts(db_session, bootstrap):
    from tests.unit._support import dispatch, new_order, repos_for

    repos = repos_for(db_session, bootstrap)
    order = new_order(repos, bootstrap)
    dispatch(repos, bootstrap, order)
    with pytest.raises(AppError) as excinfo:
        OrderService(repos).dispatch(order.id, carrier_id=None, vehicle_id=bootstrap["vehicle"].id)
    assert excinfo.value.code == ErrorCode.STATE_TRANSITION_INVALID


def test_dispatch_writes_sla_fields(db_session, bootstrap):
    from tests.unit._support import dispatch, new_order, repos_for

    repos = repos_for(db_session, bootstrap)
    order = new_order(repos, bootstrap, customer="vip")
    dispatch(repos, bootstrap, order)
    assert order.status == str(OrderStatus.DISPATCHED)
    assert order.dispatched_at is not None
    assert order.promised_delivery_at == bootstrap["promised_vip"].replace(tzinfo=None)
    assert order.original_eta_at == bootstrap["promised_vip"].replace(tzinfo=None)
    assert order.sla_rule_id is not None


def test_optimistic_lock_helper():
    class Row:
        version = 3

    row = Row()
    check_version(row, 3)
    with pytest.raises(AppError) as excinfo:
        check_version(row, 2)
    assert excinfo.value.code == ErrorCode.OPTIMISTIC_LOCK_CONFLICT
