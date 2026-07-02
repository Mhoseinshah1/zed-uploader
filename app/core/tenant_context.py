"""Per-request / per-update tenant context (Phase F1 multi-tenant isolation).

A ``ContextVar`` carries the current tenant id for the lifetime of a bot update
or a panel/API request. The DB guard (``app/db/tenant_scope.py``) reads it to
filter every ORM query and stamp every insert. A query with NO context fails
closed (raises ``NoTenantContext``) rather than silently leaking across tenants.

``ALL_TENANTS`` is an explicit, auditable bypass for trusted platform / cross-
tenant operations (tenant resolution, super-admin views, backfills).
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Iterator

# The platform's own bot is the first tenant row (seeded by migration 0019 and
# by create_all in tests). Kept as a constant for the single-tenant F1 wiring.
PLATFORM_TENANT_ID = 1

# Sentinel: run unfiltered (trusted cross-tenant / platform-global access).
ALL_TENANTS = "__all_tenants__"

_current: ContextVar[int | str | None] = ContextVar("current_tenant_id", default=None)


class NoTenantContext(RuntimeError):
    """Raised when a DB query runs with no tenant context (fail closed)."""


def set_tenant(tenant_id: int | str | None) -> Token:
    return _current.set(tenant_id)


def reset_tenant(token: Token) -> None:
    _current.reset(token)


def current_tenant() -> int | str | None:
    return _current.get()


def require_tenant() -> int:
    """Return the current tenant id or raise (used when stamping inserts)."""
    tid = _current.get()
    if not isinstance(tid, int):
        raise NoTenantContext("database write without a tenant context")
    return tid


@contextmanager
def tenant_scope(tenant_id: int) -> Iterator[None]:
    """Run a block scoped to one tenant."""
    token = _current.set(tenant_id)
    try:
        yield
    finally:
        _current.reset(token)


@contextmanager
def all_tenants() -> Iterator[None]:
    """Run a block with tenant filtering disabled (platform/cross-tenant)."""
    token = _current.set(ALL_TENANTS)
    try:
        yield
    finally:
        _current.reset(token)
