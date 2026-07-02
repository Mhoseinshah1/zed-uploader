"""F1 unit tests — token crypto, TenantService, the fail-closed guard, and the
platform-tenant seed (SQLite + fakeredis; no live DB)."""
from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.db.session  # noqa: F401 — registers the tenant guard
from app.core import crypto
from app.core.tenant_context import (
    NoTenantContext,
    all_tenants,
    reset_tenant,
    set_tenant,
    tenant_scope,
)
from app.models import Base, Media, Tenant
from app.services.tenant_service import TenantService


@pytest_asyncio.fixture
async def maker():
    engine = create_async_engine(
        "sqlite+aiosqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def test_token_crypto_roundtrip():
    token = "123456:AA-bot-father-token_xyz"
    ciphertext = crypto.encrypt_secret(token)
    assert ciphertext != token and "bot-father" not in ciphertext
    assert crypto.decrypt_secret(ciphertext) == token


async def test_platform_tenant_seeded_as_id_1(maker):
    with all_tenants():
        async with maker() as s:
            tenants = (await s.scalars(select(Tenant))).all()
    assert len(tenants) == 1
    assert tenants[0].id == 1 and tenants[0].status == "active"


async def test_tenant_service_encrypts_token_at_rest(maker):
    with all_tenants():
        async with maker() as s:
            svc = TenantService(s)
            tenant = await svc.create(
                owner_user_id=42, bot_id=999, bot_username="cust",
                bot_token="secret:token",
            )
            # stored ciphertext, not the plaintext
            assert tenant.bot_token != "secret:token"
            assert TenantService.decrypt_token(tenant) == "secret:token"
            assert (await svc.get_by_bot_id(999)).id == tenant.id


async def test_guard_fails_closed_without_context(maker):
    # clear the autouse platform context: a query with NO context must raise
    token = set_tenant(None)
    try:
        with pytest.raises(NoTenantContext):
            async with maker() as s:
                await s.scalars(select(Media))
    finally:
        reset_tenant(token)


async def test_insert_is_auto_stamped_with_current_tenant(maker):
    with all_tenants():
        async with maker() as s:
            s.add(Tenant(bot_username="t2", status="active"))
            await s.commit()
    with tenant_scope(2):
        async with maker() as s:
            s.add(Media(code="X", status="approved"))  # tenant_id not set
            await s.commit()
            media = (await s.scalars(select(Media))).all()
    assert [m.tenant_id for m in media] == [2]
