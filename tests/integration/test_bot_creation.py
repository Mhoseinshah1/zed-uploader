"""F3 (REAL Postgres) — buy-a-bot: atomic + idempotent charge→create, defaults
seeded + registered, and the rental expiry→suspend→renew lifecycle."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

import app.bot.registry as registry_mod
from app.core.tenant_context import all_tenants, tenant_scope
from app.models.admin import Admin
from app.models.plan import Plan
from app.models.tenant import Tenant
from app.models.user import User
from app.models.wallet import WalletTransaction
from app.services.bot_creation_service import BotCreationService, BotCreationStatus
from app.services.bot_plan_service import BotPlanService
from app.services.tenant_service import TenantService
from app.services.wallet_service import WalletService
from app.workers.main import process_tenant_expiry
from tests.integration.conftest import requires_pg

pytestmark = requires_pg


class FakeRegistry:
    def __init__(self):
        self.reloaded = []

    async def reload(self, tenant_id):
        self.reloaded.append(tenant_id)


async def _seed_plan(maker, *, key="perpetual", price=100, days=0):
    with all_tenants():
        async with maker() as s:
            await BotPlanService(s).upsert(key, "ربات", price, days)


async def _funded_user(maker, balance=500, tg=90001) -> int:
    with tenant_scope(1):  # platform tenant
        async with maker() as s:
            u = User(telegram_id=tg)
            s.add(u)
            await s.commit()
            if balance:
                await WalletService(s).credit(u.id, balance, reference="seed")
            return u.id


async def test_create_charges_and_creates_and_seeds(pg_sessionmaker):
    await _seed_plan(pg_sessionmaker, price=100)
    uid = await _funded_user(pg_sessionmaker, balance=500)
    reg = FakeRegistry()

    with tenant_scope(1):
        async with pg_sessionmaker() as s:
            result = await BotCreationService(s, pg_sessionmaker, reg).create_from_wallet(
                owner_user_id=uid, owner_telegram_id=90001, plan_key="perpetual",
                bot_id=700700, bot_username="mybot", bot_token="123:CUSTOMER",
            )

    assert result.status == BotCreationStatus.OK
    tid = result.tenant_id
    # charged exactly once; tenant created with encrypted token + owner
    with tenant_scope(1):
        async with pg_sessionmaker() as s:
            assert await WalletService(s).balance(uid) == 400
            debits = await s.scalar(
                select(func.count(WalletTransaction.id)).where(
                    WalletTransaction.type == "bot_purchase"
                )
            )
            assert debits == 1
    with all_tenants():
        async with pg_sessionmaker() as s:
            tenant = await TenantService(s).get(tid)
            assert tenant.bot_id == 700700 and tenant.status == "active"
            assert tenant.bot_token and tenant.bot_token != "123:CUSTOMER"
            assert TenantService.decrypt_token(tenant) == "123:CUSTOMER"
    # registered in F2's registry
    assert reg.reloaded == [tid]
    # defaults seeded under the NEW tenant: owner admin + free plan
    with tenant_scope(tid):
        async with pg_sessionmaker() as s:
            admins = (await s.scalars(select(Admin))).all()
            plans = (await s.scalars(select(Plan))).all()
    assert [a.telegram_id for a in admins] == [90001]
    assert [p.key for p in plans] == ["free"]


async def test_double_tap_makes_one_tenant_and_one_charge(pg_sessionmaker):
    await _seed_plan(pg_sessionmaker, price=100)
    uid = await _funded_user(pg_sessionmaker, balance=500)
    reg = FakeRegistry()

    async def _create():
        with tenant_scope(1):
            async with pg_sessionmaker() as s:
                return await BotCreationService(s, pg_sessionmaker, reg).create_from_wallet(
                    owner_user_id=uid, owner_telegram_id=90001, plan_key="perpetual",
                    bot_id=700701, bot_username="dup", bot_token="123:DUP",
                )

    r1 = await _create()
    r2 = await _create()
    assert r1.status == BotCreationStatus.OK
    assert r2.status in (BotCreationStatus.ALREADY_REGISTERED, BotCreationStatus.DUPLICATE)
    with all_tenants():
        async with pg_sessionmaker() as s:
            tenants = (await s.scalars(select(Tenant).where(Tenant.bot_id == 700701))).all()
    assert len(tenants) == 1
    with tenant_scope(1):
        async with pg_sessionmaker() as s:
            assert await WalletService(s).balance(uid) == 400  # charged ONCE


async def test_insufficient_funds_creates_nothing(pg_sessionmaker):
    await _seed_plan(pg_sessionmaker, price=100)
    uid = await _funded_user(pg_sessionmaker, balance=50)
    with tenant_scope(1):
        async with pg_sessionmaker() as s:
            result = await BotCreationService(s, pg_sessionmaker, None).create_from_wallet(
                owner_user_id=uid, owner_telegram_id=90001, plan_key="perpetual",
                bot_id=700702, bot_username="poor", bot_token="123:POOR",
            )
    assert result.status == BotCreationStatus.INSUFFICIENT
    with tenant_scope(1):
        async with pg_sessionmaker() as s:
            assert await WalletService(s).balance(uid) == 50  # nothing charged
    with all_tenants():
        async with pg_sessionmaker() as s:
            assert await TenantService(s).get_by_bot_id(700702) is None  # no tenant


async def test_rental_expiry_suspends_then_renew_reactivates(pg_sessionmaker, monkeypatch):
    # stop_tenant_bot builds a real Bot — stub it out
    class _StubBot:
        def __init__(self, token):
            self.session = type("S", (), {"close": _anoop})()

        async def delete_webhook(self, **kw):
            return True

    async def _anoop(*a, **k):
        return None

    monkeypatch.setattr(registry_mod, "Bot", _StubBot)

    await _seed_plan(pg_sessionmaker, key="monthly", price=100, days=30)
    uid = await _funded_user(pg_sessionmaker, balance=500)
    reg = FakeRegistry()
    with tenant_scope(1):
        async with pg_sessionmaker() as s:
            result = await BotCreationService(s, pg_sessionmaker, reg).create_from_wallet(
                owner_user_id=uid, owner_telegram_id=90001, plan_key="monthly",
                bot_id=700703, bot_username="rental", bot_token="123:RENT",
            )
    tid = result.tenant_id
    assert result.expires_at is not None  # rental sets an expiry

    # force it expired, run the worker sweep -> suspended, data kept
    with all_tenants():
        async with pg_sessionmaker() as s:
            tenant = await TenantService(s).get(tid)
            tenant.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
            await s.commit()
    suspended = await process_tenant_expiry(pg_sessionmaker)
    assert suspended >= 1
    with all_tenants():
        async with pg_sessionmaker() as s:
            assert (await TenantService(s).get(tid)).status == "suspended"

    # renew -> active again with a pushed-out expiry
    with tenant_scope(1):
        async with pg_sessionmaker() as s:
            renew = await BotCreationService(s, pg_sessionmaker, reg).renew_from_wallet(
                tenant_id=tid, owner_user_id=uid, plan_key="monthly",
            )
    assert renew.status == BotCreationStatus.OK
    with all_tenants():
        async with pg_sessionmaker() as s:
            t = await TenantService(s).get(tid)
            assert t.status == "active" and t.expires_at > datetime.now(timezone.utc)
