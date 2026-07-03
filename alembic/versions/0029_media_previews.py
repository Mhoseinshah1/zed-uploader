"""J5: media_previews — track channel auto-posts (once per media)

Revision ID: 0029_media_previews
Revises: 0028_media_thumbnail
Create Date: 2026-07-04 02:00:00.000000

The preview channel id + toggle live in per-tenant bot_settings; this table
records what was posted (and makes the auto-post idempotent).
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0029_media_previews"
down_revision: Union[str, None] = "0028_media_thumbnail"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "media_previews",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("media_id", sa.Integer(), nullable=False),
        sa.Column("channel_id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"],
            name="media_previews_tenant_id_fkey", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["media_id"], ["media.id"],
            name="media_previews_media_id_fkey", ondelete="CASCADE",
        ),
        sa.UniqueConstraint("tenant_id", "media_id", name="uq_preview_once"),
    )
    op.create_index("ix_media_previews_tenant_id", "media_previews", ["tenant_id"])
    op.create_index("ix_media_previews_media_id", "media_previews", ["media_id"])


def downgrade() -> None:
    op.drop_index("ix_media_previews_media_id", table_name="media_previews")
    op.drop_index("ix_media_previews_tenant_id", table_name="media_previews")
    op.drop_table("media_previews")
