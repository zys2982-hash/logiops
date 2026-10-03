"""add delay_minutes to exception_case

延误口径简化（docs/08）第一步：新增"人工录入的延误分钟数"字段。
本迁移**只新增**，不动 existing ETA 字段，保证过渡期内旧逻辑照常工作。

Revision ID: 9f1c2ab34d56
Revises: 831510b93e08
Create Date: 2026-10-03
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "9f1c2ab34d56"
down_revision = "831510b93e08"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("exception_case", sa.Column("delay_minutes", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("exception_case", "delay_minutes")
