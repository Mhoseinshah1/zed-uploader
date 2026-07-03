"""J9: panel_users — optional TOTP 2FA + session epoch (logout-all)

Revision ID: 0033_panel_user_2fa
Revises: 0032_custom_buttons
Create Date: 2026-07-04 06:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0033_panel_user_2fa"
down_revision: Union[str, None] = "0032_custom_buttons"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Fernet-encrypted base32 TOTP secret — nullable: 2FA is opt-in (off by default)
    op.add_column(
        "panel_users", sa.Column("totp_secret", sa.String(length=255), nullable=True)
    )
    op.add_column(
        "panel_users",
        sa.Column(
            "twofa_enabled", sa.Boolean(), server_default=sa.text("false"),
            nullable=False,
        ),
    )
    # bumping the epoch invalidates every outstanding session of that user
    op.add_column(
        "panel_users",
        sa.Column(
            "session_epoch", sa.Integer(), server_default=sa.text("0"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("panel_users", "session_epoch")
    op.drop_column("panel_users", "twofa_enabled")
    op.drop_column("panel_users", "totp_secret")
