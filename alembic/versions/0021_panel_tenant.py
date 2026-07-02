"""F4: bind panel logins to a tenant (panel_users.tenant_id, panel_audit.tenant_id)

Revision ID: 0021_panel_tenant
Revises: 0020_bot_plans
Create Date: 2026-07-02 00:00:00.000000

panel_users.tenant_id: nullable -> backfill existing logins to the platform
tenant (1) -> NOT NULL. panel_audit.tenant_id: nullable (NULL = a
platform/super-admin action). Both tables stay global (login resolves a user
before any tenant context exists).
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0021_panel_tenant"
down_revision: Union[str, None] = "0020_bot_plans"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("panel_users", sa.Column("tenant_id", sa.Integer(), nullable=True))
    op.execute("UPDATE panel_users SET tenant_id = 1")  # backfill -> platform
    op.alter_column("panel_users", "tenant_id", nullable=False)
    op.create_foreign_key(
        "panel_users_tenant_id_fkey", "panel_users", "tenants",
        ["tenant_id"], ["id"], ondelete="CASCADE",
    )
    op.create_index("ix_panel_users_tenant_id", "panel_users", ["tenant_id"])

    op.add_column("panel_audit", sa.Column("tenant_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "panel_audit_tenant_id_fkey", "panel_audit", "tenants",
        ["tenant_id"], ["id"], ondelete="SET NULL",
    )
    op.create_index("ix_panel_audit_tenant_id", "panel_audit", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_panel_audit_tenant_id", table_name="panel_audit")
    op.drop_constraint("panel_audit_tenant_id_fkey", "panel_audit", type_="foreignkey")
    op.drop_column("panel_audit", "tenant_id")

    op.drop_index("ix_panel_users_tenant_id", table_name="panel_users")
    op.drop_constraint("panel_users_tenant_id_fkey", "panel_users", type_="foreignkey")
    op.drop_column("panel_users", "tenant_id")
