"""F5: panel_users.is_superadmin — platform super-admin role

Revision ID: 0022_superadmin
Revises: 0021_panel_tenant
Create Date: 2026-07-02 00:00:00.000000

Grants the cross-tenant super-admin surface. Existing platform operators (panel
users bound to tenant 1) are backfilled to super-admin so they keep managing
the platform; customer logins (tenant >= 2) stay non-super-admin.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0022_superadmin"
down_revision: Union[str, None] = "0021_panel_tenant"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "panel_users",
        sa.Column(
            "is_superadmin", sa.Boolean(), server_default=sa.text("false"),
            nullable=False,
        ),
    )
    # existing platform operators (tenant 1) become super-admins
    op.execute("UPDATE panel_users SET is_superadmin = true WHERE tenant_id = 1")


def downgrade() -> None:
    op.drop_column("panel_users", "is_superadmin")
