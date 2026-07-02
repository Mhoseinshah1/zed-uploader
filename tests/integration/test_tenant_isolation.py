"""F1 cross-tenant isolation (REAL Postgres): the security core of multi-tenant.

Verifies that once a tenant context is set, the DB guard confines every read,
write, aggregate, and bulk statement to that tenant — and that forgetting the
context fails closed instead of leaking. Uses the composite per-tenant uniques
(shared ``code``), the wallet ledger invariant, and cross-tenant reads.
"""
from __future__ import annotations

import pytest
from sqlalchemy import func, select, update

from app.core.tenant_context import (
    NoTenantContext,
    all_tenants,
    reset_tenant,
    set_tenant,
    tenant_scope,
)
from app.models.media import Media
from app.models.tenant import Tenant
from app.models.user import User
from app.models.wallet import WalletTransaction
from app.services.media_service import MediaService
from app.services.wallet_service import WalletService
from tests.integration.conftest import requires_pg

pytestmark = requires_pg


async def _two_tenants(maker) -> tuple[int, int]:
    """Platform tenant (1, seeded) + a second tenant. Returns (t1, t2)."""
    with all_tenants():
        async with maker() as s:
            t2 = Tenant(bot_username="cust2", status="active")
            s.add(t2)
            await s.commit()
            return 1, t2.id


async def test_two_tenants_share_media_code(pg_sessionmaker):
    t1, t2 = await _two_tenants(pg_sessionmaker)
    for tid in (t1, t2):
        with tenant_scope(tid):
            async with pg_sessionmaker() as s:
                s.add(Media(code="SHARED", status="approved"))
                await s.commit()
    # both exist, one per tenant — no unique collision
    with all_tenants():
        async with pg_sessionmaker() as s:
            rows = (await s.scalars(select(Media).where(Media.code == "SHARED"))).all()
    assert sorted(m.tenant_id for m in rows) == [t1, t2]


async def test_tenant_cannot_read_or_get_other_tenants_media(pg_sessionmaker):
    t1, t2 = await _two_tenants(pg_sessionmaker)
    with tenant_scope(t1):
        async with pg_sessionmaker() as s:
            m1 = Media(code="A1", status="approved")
            s.add(m1)
            await s.commit()
            m1_id = m1.id
    with tenant_scope(t2):
        async with pg_sessionmaker() as s:
            s.add(Media(code="B1", status="approved"))
            await s.commit()

    # tenant 2 sees ONLY its own row, and cannot read tenant 1's by code or PK
    with tenant_scope(t2):
        async with pg_sessionmaker() as s:
            svc = MediaService(s)
            assert await svc.get_by_code("A1") is None  # cross-tenant code hidden
            assert await s.get(Media, m1_id) is None  # cross-tenant PK hidden
            visible = (await s.scalars(select(Media))).all()
    assert [m.code for m in visible] == ["B1"]


async def test_per_tenant_wallet_ledger_invariant(pg_sessionmaker):
    t1, t2 = await _two_tenants(pg_sessionmaker)
    # a user with the SAME telegram id in each tenant (allowed post-F1)
    with tenant_scope(t1):
        async with pg_sessionmaker() as s:
            u1 = User(telegram_id=7777)
            s.add(u1)
            await s.commit()
            await WalletService(s).credit(u1.id, 500, reference="a")
            u1_id = u1.id
    with tenant_scope(t2):
        async with pg_sessionmaker() as s:
            u2 = User(telegram_id=7777)
            s.add(u2)
            await s.commit()
            await WalletService(s).credit(u2.id, 900, reference="b")
            u2_id = u2.id

    # each tenant's ledger sums to its own user's balance; no cross-tenant bleed
    with tenant_scope(t1):
        async with pg_sessionmaker() as s:
            ledger = await s.scalar(
                select(func.coalesce(func.sum(WalletTransaction.amount), 0))
            )
            balance = await WalletService(s).balance(u1_id)
    assert ledger == 500 and balance == 500

    with tenant_scope(t2):
        async with pg_sessionmaker() as s:
            ledger = await s.scalar(
                select(func.coalesce(func.sum(WalletTransaction.amount), 0))
            )
            balance = await WalletService(s).balance(u2_id)
    assert ledger == 900 and balance == 900


async def test_bulk_update_is_tenant_scoped(pg_sessionmaker):
    t1, t2 = await _two_tenants(pg_sessionmaker)
    for tid, code in ((t1, "U1"), (t2, "U2")):
        with tenant_scope(tid):
            async with pg_sessionmaker() as s:
                s.add(Media(code=code, status="approved", is_active=True))
                await s.commit()
    # a blanket UPDATE under tenant 1 must not touch tenant 2's row
    with tenant_scope(t1):
        async with pg_sessionmaker() as s:
            await s.execute(update(Media).values(is_active=False))
            await s.commit()
    with all_tenants():
        async with pg_sessionmaker() as s:
            rows = {m.code: m.is_active for m in (await s.scalars(select(Media))).all()}
    assert rows == {"U1": False, "U2": True}


async def test_missing_context_fails_closed(pg_sessionmaker):
    await _two_tenants(pg_sessionmaker)
    token = set_tenant(None)  # clear the autouse platform context
    try:
        with pytest.raises(NoTenantContext):
            async with pg_sessionmaker() as s:
                await s.scalars(select(User))
    finally:
        reset_tenant(token)
