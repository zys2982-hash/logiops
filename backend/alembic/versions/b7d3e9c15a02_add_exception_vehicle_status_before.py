"""add vehicle_status_before to exception_case

车辆故障异常驱动车辆状态（录入 → 车辆置维修中；结束 → 恢复录入前状态）：
本迁移只**新增**一列用于记录"录入前的车辆状态"，不做行为变更。

Revision ID: b7d3e9c15a02
Revises: 9f1c2ab34d56
Create Date: 2026-10-03
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b7d3e9c15a02"
down_revision = "9f1c2ab34d56"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("exception_case", sa.Column("vehicle_status_before", sa.String(length=16), nullable=True))


def downgrade() -> None:
    op.drop_column("exception_case", "vehicle_status_before")
