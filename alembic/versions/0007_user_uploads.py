"""b1: user uploads + review workflow — status columns on media

Revision ID: 0007_user_uploads
Revises: 0006_broadcast_ledger
Create Date: 2024-07-01 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007_user_uploads"
down_revision: Union[str, None] = "0006_broadcast_ledger"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "media",
        sa.Column(
            "status", sa.String(length=16),
            server_default=sa.text("'approved'"), nullable=False,
        ),
    )
    op.add_column("media", sa.Column("reviewed_by_admin_id", sa.BigInteger(), nullable=True))
    op.add_column("media", sa.Column("review_note", sa.Text(), nullable=True))
    op.add_column("media", sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_media_status", "media", ["status"])


def downgrade() -> None:
    op.drop_index("ix_media_status", table_name="media")
    op.drop_column("media", "approved_at")
    op.drop_column("media", "review_note")
    op.drop_column("media", "reviewed_by_admin_id")
    op.drop_column("media", "status")
