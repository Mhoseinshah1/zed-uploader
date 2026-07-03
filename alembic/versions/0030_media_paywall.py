"""J6: paywall — media.required_plan/price + media_purchases entitlements

Revision ID: 0030_media_paywall
Revises: 0029_media_previews
Create Date: 2026-07-04 03:00:00.000000

The entitlement row commits atomically with the wallet debit; its
UNIQUE(tenant_id, media_id, user_id) makes the charge exactly-once.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0030_media_paywall"
down_revision: Union[str, None] = "0029_media_previews"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "media", sa.Column("required_plan", sa.String(length=32), nullable=True)
    )
    op.add_column("media", sa.Column("price", sa.BigInteger(), nullable=True))

    op.create_table(
        "media_purchases",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("media_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"],
            name="media_purchases_tenant_id_fkey", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["media_id"], ["media.id"],
            name="media_purchases_media_id_fkey", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"],
            name="media_purchases_user_id_fkey", ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "tenant_id", "media_id", "user_id", name="uq_media_purchase_once"
        ),
    )
    op.create_index("ix_media_purchases_tenant_id", "media_purchases", ["tenant_id"])
    op.create_index("ix_media_purchases_media_id", "media_purchases", ["media_id"])
    op.create_index("ix_media_purchases_user_id", "media_purchases", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_media_purchases_user_id", table_name="media_purchases")
    op.drop_index("ix_media_purchases_media_id", table_name="media_purchases")
    op.drop_index("ix_media_purchases_tenant_id", table_name="media_purchases")
    op.drop_table("media_purchases")
    op.drop_column("media", "price")
    op.drop_column("media", "required_plan")
