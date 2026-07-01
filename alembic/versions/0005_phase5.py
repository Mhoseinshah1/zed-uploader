"""phase5: CentralPay gateway columns on payments

Revision ID: 0005_phase5
Revises: 0004_phase4
Create Date: 2024-05-01 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005_phase5"
down_revision: Union[str, None] = "0004_phase4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("payments", sa.Column("provider_ref", sa.String(length=64), nullable=True))
    op.add_column("payments", sa.Column("intent", sa.String(length=32), nullable=True))


def downgrade() -> None:
    op.drop_column("payments", "intent")
    op.drop_column("payments", "provider_ref")
