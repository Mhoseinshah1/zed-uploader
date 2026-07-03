"""L1: payments — refund metadata (who / why / when)

The ``status`` column (plain varchar) additionally carries the new terminal
value ``refunded`` and the reconcile value ``expired``; no schema change is
needed for those.

Revision ID: 0034_payment_refund
Revises: 0033_panel_user_2fa
Create Date: 2026-07-04 08:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0034_payment_refund"
down_revision: Union[str, None] = "0033_panel_user_2fa"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "payments", sa.Column("refund_reason", sa.String(length=255), nullable=True)
    )
    # the panel user who performed the refund (global table -> no FK on purpose)
    op.add_column(
        "payments", sa.Column("refunded_by", sa.Integer(), nullable=True)
    )
    op.add_column(
        "payments",
        sa.Column("refunded_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("payments", "refunded_at")
    op.drop_column("payments", "refunded_by")
    op.drop_column("payments", "refund_reason")
