"""J8a: media_comments — moderated user comments (tenant-scoped)

Revision ID: 0031_media_comments
Revises: 0030_media_paywall
Create Date: 2026-07-04 04:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0031_media_comments"
down_revision: Union[str, None] = "0030_media_paywall"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "media_comments",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("media_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "status", sa.String(length=16),
            server_default=sa.text("'pending'"), nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"],
            name="media_comments_tenant_id_fkey", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["media_id"], ["media.id"],
            name="media_comments_media_id_fkey", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"],
            name="media_comments_user_id_fkey", ondelete="CASCADE",
        ),
    )
    op.create_index("ix_media_comments_tenant_id", "media_comments", ["tenant_id"])
    op.create_index("ix_media_comments_media_id", "media_comments", ["media_id"])
    op.create_index("ix_media_comments_user_id", "media_comments", ["user_id"])
    op.create_index("ix_media_comments_status", "media_comments", ["status"])


def downgrade() -> None:
    op.drop_index("ix_media_comments_status", table_name="media_comments")
    op.drop_index("ix_media_comments_user_id", table_name="media_comments")
    op.drop_index("ix_media_comments_media_id", table_name="media_comments")
    op.drop_index("ix_media_comments_tenant_id", table_name="media_comments")
    op.drop_table("media_comments")
