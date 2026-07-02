"""e1: license table

Revision ID: 0017_license
Revises: 0016_media_reports
Create Date: 2024-07-11 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0017_license"
down_revision: Union[str, None] = "0016_media_reports"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "license",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("license_key", sa.String(length=128), nullable=True),
        sa.Column(
            "status", sa.String(length=16), server_default=sa.text("'inactive'"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("allowed_install_count", sa.Integer(), nullable=True),
        sa.Column("fingerprint", sa.String(length=64), nullable=True),
        sa.Column("last_check_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_ok_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("license")
