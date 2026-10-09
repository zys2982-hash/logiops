"""add exception initial risk snapshot

建单时冻结的风险判定（口径 2026-10-08）：risk_score / level / risk_factors_json 会被
每次重算覆盖，所以另存一份不可变的 initial_* 供详情页留痕。

Revision ID: d8f1a3c02e77
Revises: c4a7f2e91b30
Create Date: 2026-10-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d8f1a3c02e77"
down_revision = "c4a7f2e91b30"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("exception_case", sa.Column("initial_risk_score", sa.Integer(), nullable=True))
    op.add_column("exception_case", sa.Column("initial_level", sa.String(length=16), nullable=True))
    op.add_column("exception_case", sa.Column("initial_risk_factors_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("exception_case", "initial_risk_factors_json")
    op.drop_column("exception_case", "initial_level")
    op.drop_column("exception_case", "initial_risk_score")
