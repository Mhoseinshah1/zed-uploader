"""phase3: monetization (wallet, plans, subscriptions, payments)

Revision ID: 0003_phase3
Revises: 0002_phase2
Create Date: 2024-03-01 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003_phase3"
down_revision: Union[str, None] = "0002_phase2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- users: monetization columns ------------------------------------
    op.add_column(
        "users",
        sa.Column("balance", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column(
        "users",
        sa.Column(
            "plan", sa.String(length=16), server_default=sa.text("'free'"), nullable=False
        ),
    )
    op.add_column(
        "users",
        sa.Column("plan_expires_at", sa.DateTime(timezone=True), nullable=True),
    )

    # --- plans -----------------------------------------------------------
    plans = op.create_table(
        "plans",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("key", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=64), nullable=False),
        sa.Column("price", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "duration_days", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("max_files", sa.Integer(), nullable=True),
        sa.Column(
            "is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_plans_key", "plans", ["key"], unique=True)

    # --- subscriptions ---------------------------------------------------
    op.create_table(
        "subscriptions",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("plan", sa.String(length=16), nullable=False),
        sa.Column(
            "starts_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_subscriptions_user_id", "subscriptions", ["user_id"])

    # --- wallet_transactions (ledger) ------------------------------------
    op.create_table(
        "wallet_transactions",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("type", sa.String(length=16), nullable=False),
        sa.Column("balance_after", sa.BigInteger(), nullable=False),
        sa.Column("reference", sa.String(length=64), nullable=True),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_wallet_transactions_user_id", "wallet_transactions", ["user_id"]
    )
    op.create_index(
        "ix_wallet_transactions_created_at", "wallet_transactions", ["created_at"]
    )

    # --- payments --------------------------------------------------------
    op.create_table(
        "payments",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("method", sa.String(length=16), nullable=False),
        sa.Column(
            "status", sa.String(length=16), server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("receipt", sa.Text(), nullable=True),
        sa.Column("admin_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_payments_user_id", "payments", ["user_id"])
    op.create_index("ix_payments_status", "payments", ["status"])

    # --- seed plans (prices 0: owner sets them before selling) -----------
    op.bulk_insert(
        plans,
        [
            {"key": "free", "title": "رایگان", "price": 0, "duration_days": 0, "max_files": 10, "is_active": True},
            {"key": "plus", "title": "پلاس", "price": 0, "duration_days": 30, "max_files": 100, "is_active": True},
            {"key": "max", "title": "مکس", "price": 0, "duration_days": 30, "max_files": None, "is_active": True},
        ],
    )

    # --- seed feature flags (plan = minimum plan that unlocks) -----------
    feature_flags = sa.table(
        "feature_flags",
        sa.column("key", sa.String),
        sa.column("is_enabled", sa.Boolean),
        sa.column("plan", sa.String),
    )
    op.bulk_insert(
        feature_flags,
        [
            {"key": "protect_content", "is_enabled": True, "plan": "plus"},
            {"key": "auto_delete", "is_enabled": True, "plan": "plus"},
            {"key": "batch_upload", "is_enabled": True, "plan": "plus"},
        ],
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM feature_flags WHERE key IN "
        "('protect_content','auto_delete','batch_upload')"
    )
    op.drop_index("ix_payments_status", table_name="payments")
    op.drop_index("ix_payments_user_id", table_name="payments")
    op.drop_table("payments")
    op.drop_index("ix_wallet_transactions_created_at", table_name="wallet_transactions")
    op.drop_index("ix_wallet_transactions_user_id", table_name="wallet_transactions")
    op.drop_table("wallet_transactions")
    op.drop_index("ix_subscriptions_user_id", table_name="subscriptions")
    op.drop_table("subscriptions")
    op.drop_index("ix_plans_key", table_name="plans")
    op.drop_table("plans")
    op.drop_column("users", "plan_expires_at")
    op.drop_column("users", "plan")
    op.drop_column("users", "balance")
