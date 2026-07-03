"""J8b: custom_buttons — tenant-defined reply-keyboard buttons

Revision ID: 0032_custom_buttons
Revises: 0031_media_comments
Create Date: 2026-07-04 05:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0032_custom_buttons"
down_revision: Union[str, None] = "0031_media_comments"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "custom_buttons",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(length=64), nullable=False),
        sa.Column("type", sa.String(length=16), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"],
            name="custom_buttons_tenant_id_fkey", ondelete="CASCADE",
        ),
    )
    op.create_index("ix_custom_buttons_tenant_id", "custom_buttons", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_custom_buttons_tenant_id", table_name="custom_buttons")
    op.drop_table("custom_buttons")
