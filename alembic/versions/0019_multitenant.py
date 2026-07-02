"""F1: multi-tenant foundation — tenants table + tenant_id on every scoped table

Revision ID: 0019_multitenant
Revises: 0018_bot_commands
Create Date: 2026-07-02 00:00:00.000000

Adds the ``tenants`` registry, seeds the platform tenant (id=1) for the existing
single-tenant deployment, then threads a NOT NULL ``tenant_id`` FK through every
tenant-scoped table. Existing rows are backfilled to the platform tenant BEFORE
the column is made NOT NULL, so this upgrades cleanly over live data. Per-tenant
uniqueness: single-column unique indexes on ``code`` / ``telegram_id`` / ``key``
and the Stars charge / bot-command constraints become composite ``(tenant_id,
…)`` so two bots never collide.

Global tables kept UNSCOPED (and why): ``tenants`` (it is the registry),
``panel_users`` / ``panel_audit`` (platform login accounts — mapped to tenants
in a later phase), ``license`` (one platform license), ``alembic_version``.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0019_multitenant"
down_revision: Union[str, None] = "0018_bot_commands"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# every tenant-scoped table (gets a NOT NULL tenant_id FK + ix_<t>_tenant_id).
SCOPED_TABLES = (
    "admins", "ads", "backup_jobs", "bot_commands", "bot_settings",
    "broadcast_jobs", "broadcast_recipients", "download_logs", "feature_flags",
    "folders", "media", "media_files", "media_reports", "payment_providers",
    "payments", "plans", "required_channels", "subscriptions", "users",
    "wallet_transactions",
)


def upgrade() -> None:
    # --- 1) the tenant registry ---------------------------------------------
    op.create_table(
        "tenants",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("owner_user_id", sa.BigInteger(), nullable=True),
        sa.Column("bot_id", sa.BigInteger(), nullable=True),
        sa.Column("bot_username", sa.String(length=64), nullable=True),
        sa.Column("bot_token", sa.String(length=512), nullable=True),
        sa.Column("webhook_secret", sa.String(length=64), nullable=True),
        sa.Column(
            "status", sa.String(length=16), server_default=sa.text("'active'"),
            nullable=False,
        ),
        sa.Column("plan", sa.String(length=32), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_tenants_bot_id", "tenants", ["bot_id"], unique=True)
    op.create_index("ix_tenants_status", "tenants", ["status"], unique=False)

    # --- 2) seed the platform tenant as the FIRST row (id=1) ----------------
    # The existing live bot becomes tenant 1; every existing row backfills to it.
    op.execute(
        "INSERT INTO tenants (bot_username, status) VALUES ('platform', 'active')"
    )

    # --- 3) thread tenant_id through every scoped table ---------------------
    for table in SCOPED_TABLES:
        op.add_column(table, sa.Column("tenant_id", sa.Integer(), nullable=True))
        op.execute(f"UPDATE {table} SET tenant_id = 1")  # backfill -> platform
        op.alter_column(table, "tenant_id", nullable=False)
        op.create_foreign_key(
            f"{table}_tenant_id_fkey", table, "tenants",
            ["tenant_id"], ["id"], ondelete="CASCADE",
        )
        op.create_index(f"ix_{table}_tenant_id", table, ["tenant_id"], unique=False)

    # --- 4) per-tenant uniqueness (drop single-col unique, add composite) ----
    op.drop_index("ix_users_telegram_id", table_name="users")
    op.create_index(
        "uq_users_tenant_telegram", "users", ["tenant_id", "telegram_id"], unique=True
    )

    op.drop_index("ix_admins_telegram_id", table_name="admins")
    op.create_index(
        "uq_admins_tenant_telegram", "admins", ["tenant_id", "telegram_id"], unique=True
    )

    op.drop_index("ix_media_code", table_name="media")
    op.create_index(
        "uq_media_tenant_code", "media", ["tenant_id", "code"], unique=True
    )

    op.drop_index("ix_bot_settings_key", table_name="bot_settings")
    op.create_index(
        "uq_bot_settings_tenant_key", "bot_settings", ["tenant_id", "key"], unique=True
    )

    op.drop_index("ix_feature_flags_key", table_name="feature_flags")
    op.create_index(
        "uq_feature_flags_tenant_key", "feature_flags", ["tenant_id", "key"],
        unique=True,
    )

    op.drop_index("ix_plans_key", table_name="plans")
    op.create_index(
        "uq_plans_tenant_key", "plans", ["tenant_id", "key"], unique=True
    )

    op.drop_index("ix_payment_providers_key", table_name="payment_providers")
    op.create_index(
        "uq_payment_providers_tenant_key", "payment_providers", ["tenant_id", "key"],
        unique=True,
    )

    # Stars charge-id idempotency -> per tenant.
    op.drop_index("uq_payments_stars_charge", table_name="payments")
    op.create_index(
        "uq_payments_stars_charge", "payments", ["tenant_id", "provider_ref"],
        unique=True,
        postgresql_where=sa.text("method = 'telegram_stars'"),
        sqlite_where=sa.text("method = 'telegram_stars'"),
    )

    op.drop_constraint(
        "uq_bot_commands_scope_command", "bot_commands", type_="unique"
    )
    op.create_unique_constraint(
        "uq_bot_commands_tenant_scope_command", "bot_commands",
        ["tenant_id", "scope", "command"],
    )


def downgrade() -> None:
    # reverse the per-tenant uniqueness first
    op.drop_constraint(
        "uq_bot_commands_tenant_scope_command", "bot_commands", type_="unique"
    )
    op.create_unique_constraint(
        "uq_bot_commands_scope_command", "bot_commands", ["scope", "command"]
    )

    op.drop_index("uq_payments_stars_charge", table_name="payments")
    op.create_index(
        "uq_payments_stars_charge", "payments", ["provider_ref"], unique=True,
        postgresql_where=sa.text("method = 'telegram_stars'"),
        sqlite_where=sa.text("method = 'telegram_stars'"),
    )

    op.drop_index("uq_payment_providers_tenant_key", table_name="payment_providers")
    op.create_index(
        "ix_payment_providers_key", "payment_providers", ["key"], unique=True
    )

    op.drop_index("uq_plans_tenant_key", table_name="plans")
    op.create_index("ix_plans_key", "plans", ["key"], unique=True)

    op.drop_index("uq_feature_flags_tenant_key", table_name="feature_flags")
    op.create_index("ix_feature_flags_key", "feature_flags", ["key"], unique=True)

    op.drop_index("uq_bot_settings_tenant_key", table_name="bot_settings")
    op.create_index("ix_bot_settings_key", "bot_settings", ["key"], unique=True)

    op.drop_index("uq_media_tenant_code", table_name="media")
    op.create_index("ix_media_code", "media", ["code"], unique=True)

    op.drop_index("uq_admins_tenant_telegram", table_name="admins")
    op.create_index("ix_admins_telegram_id", "admins", ["telegram_id"], unique=True)

    op.drop_index("uq_users_tenant_telegram", table_name="users")
    op.create_index("ix_users_telegram_id", "users", ["telegram_id"], unique=True)

    for table in reversed(SCOPED_TABLES):
        op.drop_index(f"ix_{table}_tenant_id", table_name=table)
        op.drop_constraint(f"{table}_tenant_id_fkey", table, type_="foreignkey")
        op.drop_column(table, "tenant_id")

    op.drop_index("ix_tenants_status", table_name="tenants")
    op.drop_index("ix_tenants_bot_id", table_name="tenants")
    op.drop_table("tenants")
