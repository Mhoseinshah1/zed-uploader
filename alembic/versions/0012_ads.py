"""c2: ads table

Revision ID: 0012_ads
Revises: 0011_provider_config
Create Date: 2024-07-06 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0012_ads"
down_revision: Union[str, None] = "0011_provider_config"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ads",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("title", sa.String(length=128), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("button_text", sa.String(length=64), nullable=True),
        sa.Column("button_url", sa.String(length=512), nullable=True),
        sa.Column("placement", sa.String(length=16), nullable=False),
        sa.Column("target_plan", sa.String(length=16), nullable=True),
        sa.Column(
            "is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column("impression_limit", sa.Integer(), nullable=True),
        sa.Column(
            "impression_count", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "click_count", sa.Integer(), server_default=sa.text("0"), nullable=False
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
    op.create_index("ix_ads_placement", "ads", ["placement"])


def downgrade() -> None:
    op.drop_index("ix_ads_placement", table_name="ads")
    op.drop_table("ads")
