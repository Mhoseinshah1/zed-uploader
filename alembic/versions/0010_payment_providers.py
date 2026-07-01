"""c1: gateway seam — payment_providers table + payments.provider/authority

Revision ID: 0010_payment_providers
Revises: 0009_media_search_indexes
Create Date: 2024-07-04 00:00:00.000000

Seeds one config row per known provider: centralpay enabled (its actual
availability still requires the env API keys, unchanged), zarinpal disabled
until an owner sets a merchant id in the panel. Existing CentralPay payment
rows keep provider NULL — resolution falls back to `method`.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0010_payment_providers"
down_revision: Union[str, None] = "0009_media_search_indexes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    providers = op.create_table(
        "payment_providers",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("key", sa.String(length=32), nullable=False),
        sa.Column(
            "is_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("merchant_id", sa.String(length=64), nullable=True),
        sa.Column(
            "sandbox", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("extra", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_payment_providers_key", "payment_providers", ["key"], unique=True)

    op.add_column("payments", sa.Column("provider", sa.String(length=32), nullable=True))
    op.add_column("payments", sa.Column("authority", sa.String(length=64), nullable=True))
    op.create_index("ix_payments_authority", "payments", ["authority"])

    op.bulk_insert(
        providers,
        [
            {"key": "centralpay", "is_enabled": True, "merchant_id": None, "sandbox": False},
            {"key": "zarinpal", "is_enabled": False, "merchant_id": None, "sandbox": False},
        ],
    )


def downgrade() -> None:
    op.drop_index("ix_payments_authority", table_name="payments")
    op.drop_column("payments", "authority")
    op.drop_column("payments", "provider")
    op.drop_index("ix_payment_providers_key", table_name="payment_providers")
    op.drop_table("payment_providers")
