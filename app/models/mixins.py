"""Shared SQLAlchemy mixins."""
from __future__ import annotations

from sqlalchemy import ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column


class TenantScoped:
    """Every tenant-owned table carries an indexed NOT NULL ``tenant_id``.

    The DB guard (``app/db/tenant_scope.py``) filters every query and stamps
    every insert by this column, keyed off the current tenant context — so
    forgetting to scope a query fails closed instead of leaking cross-tenant
    rows. The FK is auto-named (PG default ``<table>_tenant_id_fkey``) because
    the name differs per table; ``ondelete=CASCADE`` removes a tenant's rows
    with the tenant.
    """

    tenant_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("tenants.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
