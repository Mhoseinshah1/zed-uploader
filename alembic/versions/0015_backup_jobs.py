"""d1: backup_jobs table

Revision ID: 0015_backup_jobs
Revises: 0014_telegram_stars
Create Date: 2024-07-09 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0015_backup_jobs"
down_revision: Union[str, None] = "0014_telegram_stars"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "backup_jobs",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column(
            "type", sa.String(length=16), server_default=sa.text("'manual'"),
            nullable=False,
        ),
        sa.Column(
            "status", sa.String(length=16), server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("file_path", sa.String(length=512), nullable=True),
        sa.Column("file_size", sa.BigInteger(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_by_admin_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_backup_jobs_status", "backup_jobs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_backup_jobs_status", table_name="backup_jobs")
    op.drop_table("backup_jobs")
