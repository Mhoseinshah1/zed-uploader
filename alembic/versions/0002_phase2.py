"""phase2: required_channels

Revision ID: 0002_phase2
Revises: 0001_initial
Create Date: 2024-02-01 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002_phase2"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "required_channels",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("chat_id", sa.String(length=255), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("invite_link", sa.String(length=512), nullable=True),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_required_channels_is_active", "required_channels", ["is_active"]
    )


def downgrade() -> None:
    op.drop_index("ix_required_channels_is_active", table_name="required_channels")
    op.drop_table("required_channels")
