"""G1: tenant_log_settings — per-tenant Telegram log group + topic ids

Revision ID: 0023_tenant_log_settings
Revises: 0022_superadmin
Create Date: 2026-07-02 00:00:00.000000

Tenant-scoped (tenant_id FK + guard); one row per tenant.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0023_tenant_log_settings"
down_revision: Union[str, None] = "0022_superadmin"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tenant_log_settings",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("log_group_id", sa.BigInteger(), nullable=True),
        sa.Column("topic_payments", sa.Integer(), nullable=True),
        sa.Column("topic_uploads", sa.Integer(), nullable=True),
        sa.Column("topic_errors", sa.Integer(), nullable=True),
        sa.Column("topic_new_users", sa.Integer(), nullable=True),
        sa.Column("topic_backups", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="tenant_log_settings_tenant_id_fkey",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("tenant_id", name="uq_tenant_log_settings_tenant"),
    )
    op.create_index(
        "ix_tenant_log_settings_tenant_id", "tenant_log_settings", ["tenant_id"]
    )


def downgrade() -> None:
    op.drop_index(
        "ix_tenant_log_settings_tenant_id", table_name="tenant_log_settings"
    )
    op.drop_table("tenant_log_settings")
