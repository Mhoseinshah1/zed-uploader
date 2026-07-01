"""c1b: per-provider config JSON + zibal provider row

Revision ID: 0011_provider_config
Revises: 0010_payment_providers
Create Date: 2024-07-05 00:00:00.000000

Adds the flexible ``config`` column each gateway stores its own credentials in
(zarinpal: merchant_id, zibal: merchant, centralpay: getlink_key/verify_key)
and seeds the zibal row (disabled). The centralpay/zarinpal rows from 0010 are
left untouched so existing deployments keep working unchanged.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0011_provider_config"
down_revision: Union[str, None] = "0010_payment_providers"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("payment_providers", sa.Column("config", sa.JSON(), nullable=True))
    providers = sa.table(
        "payment_providers",
        sa.column("key", sa.String),
        sa.column("is_enabled", sa.Boolean),
        sa.column("sandbox", sa.Boolean),
    )
    op.bulk_insert(
        providers,
        [{"key": "zibal", "is_enabled": False, "sandbox": False}],
    )


def downgrade() -> None:
    op.execute("DELETE FROM payment_providers WHERE key = 'zibal'")
    op.drop_column("payment_providers", "config")
