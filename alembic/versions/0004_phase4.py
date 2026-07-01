"""phase4: web panel (panel_users, panel_audit)

Revision ID: 0004_phase4
Revises: 0003_phase3
Create Date: 2024-04-01 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004_phase4"
down_revision: Union[str, None] = "0003_phase3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "panel_users",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column(
            "is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_panel_users_username", "panel_users", ["username"], unique=True)

    op.create_table(
        "panel_audit",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("panel_user_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("target", sa.String(length=255), nullable=True),
        sa.Column("ip", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_panel_audit_created_at", "panel_audit", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_panel_audit_created_at", table_name="panel_audit")
    op.drop_table("panel_audit")
    op.drop_index("ix_panel_users_username", table_name="panel_users")
    op.drop_table("panel_users")
