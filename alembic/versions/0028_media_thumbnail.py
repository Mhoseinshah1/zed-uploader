"""J4: media.thumbnail_file_id — optional video cover

Revision ID: 0028_media_thumbnail
Revises: 0027_media_reactions
Create Date: 2026-07-04 01:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0028_media_thumbnail"
down_revision: Union[str, None] = "0027_media_reactions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "media",
        sa.Column("thumbnail_file_id", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("media", "thumbnail_file_id")
