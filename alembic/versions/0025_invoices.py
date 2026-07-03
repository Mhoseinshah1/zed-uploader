"""H4: invoices — one receipt per settled payment/charge (tenant-scoped)

Revision ID: 0025_invoices
Revises: 0024_support_tickets
Create Date: 2026-07-03 01:00:00.000000

Tenant-scoped. ``invoice_no`` is sequential per tenant (uq_invoice_no) and
``source_ref`` is the settlement's idempotency key (uq_invoice_source) so a
retry / double-callback can never create a duplicate.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0025_invoices"
down_revision: Union[str, None] = "0024_support_tickets"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "invoices",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("method", sa.String(length=32), nullable=False),
        sa.Column("provider_ref", sa.String(length=128), nullable=True),
        sa.Column("source_ref", sa.String(length=128), nullable=False),
        sa.Column("invoice_no", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"],
            name="invoices_tenant_id_fkey", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"],
            name="invoices_user_id_fkey", ondelete="CASCADE",
        ),
        sa.UniqueConstraint("tenant_id", "source_ref", name="uq_invoice_source"),
        sa.UniqueConstraint("tenant_id", "invoice_no", name="uq_invoice_no"),
    )
    op.create_index("ix_invoices_tenant_id", "invoices", ["tenant_id"])
    op.create_index("ix_invoices_user_id", "invoices", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_invoices_user_id", table_name="invoices")
    op.drop_index("ix_invoices_tenant_id", table_name="invoices")
    op.drop_table("invoices")
