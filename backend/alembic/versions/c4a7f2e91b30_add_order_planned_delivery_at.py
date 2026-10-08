"""add planned_delivery_at to order

「预计到达时间」（2026-10-08 用户需求）：
运营在订单详情页手工登记的**独立字段**，与承诺到达（SLA 规则算）、实际送达（订单事实）都不同。
本次只加字段：不产生轨迹事件、不进运输轨迹时间线，也不参与现有延误判定；
后续若要把它用于延误逻辑（例如用"预送达"替代"实际送达"做判定）另开改动。

Revision ID: c4a7f2e91b30
Revises: b7d3e9c15a02
Create Date: 2026-10-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c4a7f2e91b30"
down_revision = "b7d3e9c15a02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("order", sa.Column("planned_delivery_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("order", "planned_delivery_at")
