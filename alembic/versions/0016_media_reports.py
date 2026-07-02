"""d2: media_reports table

Revision ID: 0016_media_reports
Revises: 0015_backup_jobs
Create Date: 2024-07-10 00:00:00.000000

UNIQUE(media_id, user_id): one report per user per media (dedup policy).
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0016_media_reports"
down_revision: Union[str, None] = "0015_backup_jobs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "media_reports",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("media_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(length=32), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "status", sa.String(length=16), server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("reviewed_by_admin_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["media_id"], ["media.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("media_id", "user_id", name="uq_media_report_once"),
    )
    op.create_index("ix_media_reports_media_id", "media_reports", ["media_id"])
    op.create_index("ix_media_reports_status", "media_reports", ["status"])


def downgrade() -> None:
    op.drop_index("ix_media_reports_status", table_name="media_reports")
    op.drop_index("ix_media_reports_media_id", table_name="media_reports")
    op.drop_table("media_reports")
