"""b2: folders — folders table + media.folder_id (ON DELETE SET NULL)

Revision ID: 0008_folders
Revises: 0007_user_uploads
Create Date: 2024-07-02 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0008_folders"
down_revision: Union[str, None] = "0007_user_uploads"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "folders",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("parent_id", sa.Integer(), nullable=True),
        sa.Column("owner_admin_id", sa.BigInteger(), nullable=True),
        sa.Column("sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["parent_id"], ["folders.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_folders_parent_id", "folders", ["parent_id"])

    op.add_column("media", sa.Column("folder_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_media_folder_id", "media", "folders", ["folder_id"], ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_media_folder_id", "media", ["folder_id"])


def downgrade() -> None:
    op.drop_index("ix_media_folder_id", table_name="media")
    op.drop_constraint("fk_media_folder_id", "media", type_="foreignkey")
    op.drop_column("media", "folder_id")
    op.drop_index("ix_folders_parent_id", table_name="folders")
    op.drop_table("folders")
