"""b3: search indexes — pg_trgm GIN on media.title/caption

Revision ID: 0009_media_search_indexes
Revises: 0008_folders
Create Date: 2024-07-03 00:00:00.000000

``media.code`` already carries a unique btree from the initial migration; this
adds trigram GIN indexes so substring ILIKE search on title/caption is fast.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0009_media_search_indexes"
down_revision: Union[str, None] = "0008_folders"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.create_index(
        "ix_media_title_trgm", "media", ["title"],
        postgresql_using="gin", postgresql_ops={"title": "gin_trgm_ops"},
    )
    op.create_index(
        "ix_media_caption_trgm", "media", ["caption"],
        postgresql_using="gin", postgresql_ops={"caption": "gin_trgm_ops"},
    )


def downgrade() -> None:
    op.drop_index("ix_media_caption_trgm", table_name="media")
    op.drop_index("ix_media_title_trgm", table_name="media")
