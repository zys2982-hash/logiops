"""服务层：编排规则 + 持久化 + 审计。Router 只调这里，不写业务逻辑。

本包由后端域智能体实现，公开契约（其他模块/AI 只能依赖这些名字）：

orders.OrderService
    dispatch(order_id, *, carrier_id, vehicle_id, driver_id=None, actor_id=None) -> Order
    update_eta(order_id, *, eta_at, reason, actor_id=None, source=...) -> Order
    append_tracking(order_id, *, event_type, city, occurred_at=None, source=..., speed_kmh=None,
                    address=None, payload=None, actor_id=None) -> TrackingEvent   # 触发 ETA 重算与检测
    mark_delivered(order_id, *, occurred_at=None, actor_id=None) -> Order

sla  -> 见 app.rules.sla（纯规则），服务层只负责取规则与落库
eta  -> 见 app.rules.eta（纯规则）
risk -> 见 app.rules.risk（纯规则）
detection -> 见 app.rules.detection（纯规则决策）

exceptions.ExceptionService
    create_manual(...) / confirm(...) / request_analysis(...) / apply_analysis_result(...)
    resolve(...) / close(...) / add_message(...) / detail(...) / timeline(...)

approvals.ApprovalExecutor
    approve(approval_id, *, actor_id, final_payload, expected_version)  -> dict(execution_result)
    reject(approval_id, *, actor_id, reason, expected_version)
    retry(approval_id, *, actor_id)

approvals.build_approvals_from_analysis(repos, analysis, actor_id) -> list[Approval]

tick.run_tick(session, repos, *, workspace_id, minutes) -> dict
tick.advance_until_delivered(session, repos, *, workspace_id) -> dict

followups.FollowupService / notifications.NotificationService
    （创建、完成、批准、模拟发送，均写审计）

read_models：只读聚合视图（AI Tool 专用），已由 Lead 实现。
"""

from __future__ import annotations

__all__ = ["read_models"]
