"""a3: broadcast ledger (exactly-once) — broadcast_jobs + broadcast_recipients

Revision ID: 0006_broadcast_ledger
Revises: 0005_phase5
Create Date: 2024-06-01 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006_broadcast_ledger"
down_revision: Union[str, None] = "0005_phase5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "broadcast_jobs",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("from_chat_id", sa.BigInteger(), nullable=True),
        sa.Column("message_id", sa.BigInteger(), nullable=True),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column(
            "status", sa.String(length=16), server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("total", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("sent", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("failed", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("blocked", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_broadcast_jobs_status", "broadcast_jobs", ["status"])

    op.create_table(
        "broadcast_recipients",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("broadcast_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "status", sa.String(length=16), server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("error_message", sa.String(length=255), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["broadcast_id"], ["broadcast_jobs.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "broadcast_id", "user_id", name="uq_broadcast_recipient"
        ),
    )
    op.create_index(
        "ix_broadcast_recipients_job_status",
        "broadcast_recipients",
        ["broadcast_id", "status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_broadcast_recipients_job_status", table_name="broadcast_recipients"
    )
    op.drop_table("broadcast_recipients")
    op.drop_index("ix_broadcast_jobs_status", table_name="broadcast_jobs")
    op.drop_table("broadcast_jobs")
