"""c4: telegram stars — plans.stars_price + charge-id idempotency index

Revision ID: 0014_telegram_stars
Revises: 0013_stats_indexes
Create Date: 2024-07-08 00:00:00.000000

The partial unique index makes the Telegram charge id a DB-enforced
idempotency key for Stars payments (NULL/other methods unaffected).
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0014_telegram_stars"
down_revision: Union[str, None] = "0013_stats_indexes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("plans", sa.Column("stars_price", sa.Integer(), nullable=True))
    op.create_index(
        "uq_payments_stars_charge",
        "payments",
        ["provider_ref"],
        unique=True,
        postgresql_where=sa.text("method = 'telegram_stars'"),
    )


def downgrade() -> None:
    op.drop_index("uq_payments_stars_charge", table_name="payments")
    op.drop_column("plans", "stars_price")
