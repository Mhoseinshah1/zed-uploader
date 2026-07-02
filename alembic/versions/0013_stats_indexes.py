"""c3: stats index — download_logs.created_at

Revision ID: 0013_stats_indexes
Revises: 0012_ads
Create Date: 2024-07-07 00:00:00.000000

downloads-per-day aggregates range-scan download_logs (the largest table) by
created_at; the other stats tables already carry the indexes they need.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0013_stats_indexes"
down_revision: Union[str, None] = "0012_ads"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index("ix_download_logs_created_at", "download_logs", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_download_logs_created_at", table_name="download_logs")
