"""J1: media_reactions + denormalized counters on media

Revision ID: 0027_media_reactions
Revises: 0026_panel_roles
Create Date: 2026-07-04 00:00:00.000000

One reaction per (tenant, media, user, kind); counters live on media and are
updated atomically with the reaction row (popular-sort reads the counters —
cheaper than a COUNT join on every listing).
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0027_media_reactions"
down_revision: Union[str, None] = "0026_panel_roles"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "media_reactions",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("media_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"],
            name="media_reactions_tenant_id_fkey", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["media_id"], ["media.id"],
            name="media_reactions_media_id_fkey", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"],
            name="media_reactions_user_id_fkey", ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "tenant_id", "media_id", "user_id", "kind", name="uq_reaction_once"
        ),
    )
    op.create_index("ix_media_reactions_tenant_id", "media_reactions", ["tenant_id"])
    op.create_index("ix_media_reactions_media_id", "media_reactions", ["media_id"])
    op.create_index("ix_media_reactions_user_id", "media_reactions", ["user_id"])

    op.add_column(
        "media",
        sa.Column("like_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column(
        "media",
        sa.Column("dislike_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column(
        "media",
        sa.Column("favorite_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("media", "favorite_count")
    op.drop_column("media", "dislike_count")
    op.drop_column("media", "like_count")
    op.drop_index("ix_media_reactions_user_id", table_name="media_reactions")
    op.drop_index("ix_media_reactions_media_id", table_name="media_reactions")
    op.drop_index("ix_media_reactions_tenant_id", table_name="media_reactions")
    op.drop_table("media_reactions")
