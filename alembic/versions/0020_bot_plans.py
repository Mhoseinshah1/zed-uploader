"""F3: bot_plans — master-bot pricing for the buy-a-bot flow

Revision ID: 0020_bot_plans
Revises: 0019_multitenant
Create Date: 2026-07-02 00:00:00.000000

Global (platform-level) pricing table. Seeds one perpetual plan so the flow
works out of the box; the platform admin edits price/adds rentals in the panel.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0020_bot_plans"
down_revision: Union[str, None] = "0019_multitenant"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "bot_plans",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("key", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=64), nullable=False),
        sa.Column(
            "price", sa.BigInteger(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "duration_days", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_bot_plans_key", "bot_plans", ["key"], unique=True)
    # a sensible default perpetual plan (price editable in the platform panel)
    op.execute(
        "INSERT INTO bot_plans (key, title, price, duration_days, is_active) "
        "VALUES ('perpetual', 'ربات دائمی', 100000, 0, true)"
    )


def downgrade() -> None:
    op.drop_index("ix_bot_plans_key", table_name="bot_plans")
    op.drop_table("bot_plans")
