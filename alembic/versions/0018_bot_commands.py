"""menu: bot_commands table

Revision ID: 0018_bot_commands
Revises: 0017_license
Create Date: 2024-07-12 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0018_bot_commands"
down_revision: Union[str, None] = "0017_license"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "bot_commands",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("scope", sa.String(length=8), nullable=False),
        sa.Column("command", sa.String(length=32), nullable=False),
        sa.Column("description", sa.String(length=256), nullable=False),
        sa.Column(
            "sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("scope", "command", name="uq_bot_commands_scope_command"),
    )


def downgrade() -> None:
    op.drop_table("bot_commands")
