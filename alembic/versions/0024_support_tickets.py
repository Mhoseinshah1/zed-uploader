"""H2: support_tickets + ticket_messages — in-bot ticketing (tenant-scoped)

Revision ID: 0024_support_tickets
Revises: 0023_tenant_log_settings
Create Date: 2026-07-03 00:00:00.000000

Both tables are tenant-scoped (tenant_id FK + guard). A ticket's target is
tenant_admin (end-user → tenant admin) or platform (reseller → platform owner,
surfaced only in the super-admin inbox).
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0024_support_tickets"
down_revision: Union[str, None] = "0023_tenant_log_settings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "support_tickets",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("opener_user_id", sa.Integer(), nullable=False),
        sa.Column("subject", sa.String(length=200), nullable=False),
        sa.Column(
            "status", sa.String(length=16),
            server_default=sa.text("'open'"), nullable=False,
        ),
        sa.Column(
            "target", sa.String(length=16),
            server_default=sa.text("'tenant_admin'"), nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"],
            name="support_tickets_tenant_id_fkey", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["opener_user_id"], ["users.id"],
            name="support_tickets_opener_user_id_fkey", ondelete="CASCADE",
        ),
    )
    op.create_index("ix_support_tickets_tenant_id", "support_tickets", ["tenant_id"])
    op.create_index(
        "ix_support_tickets_opener_user_id", "support_tickets", ["opener_user_id"]
    )
    op.create_index("ix_support_tickets_status", "support_tickets", ["status"])
    op.create_index("ix_support_tickets_target", "support_tickets", ["target"])

    op.create_table(
        "ticket_messages",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("ticket_id", sa.Integer(), nullable=False),
        sa.Column("sender_kind", sa.String(length=8), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"],
            name="ticket_messages_tenant_id_fkey", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["ticket_id"], ["support_tickets.id"],
            name="ticket_messages_ticket_id_fkey", ondelete="CASCADE",
        ),
    )
    op.create_index("ix_ticket_messages_tenant_id", "ticket_messages", ["tenant_id"])
    op.create_index("ix_ticket_messages_ticket_id", "ticket_messages", ["ticket_id"])


def downgrade() -> None:
    op.drop_index("ix_ticket_messages_ticket_id", table_name="ticket_messages")
    op.drop_index("ix_ticket_messages_tenant_id", table_name="ticket_messages")
    op.drop_table("ticket_messages")
    op.drop_index("ix_support_tickets_target", table_name="support_tickets")
    op.drop_index("ix_support_tickets_status", table_name="support_tickets")
    op.drop_index("ix_support_tickets_opener_user_id", table_name="support_tickets")
    op.drop_index("ix_support_tickets_tenant_id", table_name="support_tickets")
    op.drop_table("support_tickets")
