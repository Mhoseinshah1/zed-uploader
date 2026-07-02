"""Global ORM guard enforcing tenant isolation (Phase F1).

Two events on the shared SQLAlchemy ``Session`` (which ``AsyncSession`` wraps):

- ``do_orm_execute``: every SELECT / UPDATE / DELETE touching a ``TenantScoped``
  model is filtered to the current tenant via ``with_loader_criteria``. With no
  tenant context it raises (fail closed); under ``ALL_TENANTS`` or the
  ``all_tenants`` execution option it runs unfiltered.
- ``before_flush``: every new ``TenantScoped`` row is stamped with the current
  tenant id (so callers never have to remember to set it).

Global tables (Tenant, PanelUser, PanelAudit, LicenseInfo) don't inherit
``TenantScoped`` so the loader criteria never matches them — they pass through
under any real or ALL_TENANTS context.
"""
from __future__ import annotations

from sqlalchemy import event
from sqlalchemy.orm import Session, with_loader_criteria

from app.core.tenant_context import (
    ALL_TENANTS,
    NoTenantContext,
    current_tenant,
    require_tenant,
)
from app.models.mixins import TenantScoped


@event.listens_for(Session, "do_orm_execute")
def _apply_tenant_filter(state) -> None:
    # Skip lazy column/relationship loads: the parent query was already scoped,
    # and re-applying criteria to these interferes with relationship loading.
    if state.is_column_load or state.is_relationship_load:
        return
    if not (state.is_select or state.is_update or state.is_delete):
        return
    if state.execution_options.get("all_tenants"):
        return
    ctx = current_tenant()
    if ctx == ALL_TENANTS:
        return
    if not isinstance(ctx, int):
        raise NoTenantContext("database query without a tenant context")
    # ctx is captured as a bound parameter, so the criteria is cached across
    # tenants and re-bound per execution (no stale-value caching bug).
    state.statement = state.statement.options(
        with_loader_criteria(
            TenantScoped,
            lambda cls: cls.tenant_id == ctx,
            include_aliases=True,
        )
    )


@event.listens_for(Session, "before_flush")
def _stamp_tenant(session, flush_context, instances) -> None:
    tenant_id: int | None = None
    for obj in session.new:
        if isinstance(obj, TenantScoped) and obj.tenant_id is None:
            if tenant_id is None:
                tenant_id = require_tenant()  # raises if no/ALL context
            obj.tenant_id = tenant_id
