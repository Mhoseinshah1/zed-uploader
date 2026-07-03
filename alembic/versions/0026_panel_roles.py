"""I2: panel_users.role — per-tenant panel roles

Revision ID: 0026_panel_roles
Revises: 0025_invoices
Create Date: 2026-07-03 02:00:00.000000

Adds a ``role`` column to panel_users (owner|admin|support|finance|content).
Existing rows backfill to ``owner`` via the server default (no behaviour change
for current logins). ``is_superadmin`` (platform) stays orthogonal.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0026_panel_roles"
down_revision: Union[str, None] = "0025_invoices"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "panel_users",
        sa.Column(
            "role", sa.String(length=16),
            server_default=sa.text("'owner'"), nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("panel_users", "role")
