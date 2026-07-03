"""J6 — paywall: plan gate, one-time purchase (exactly once), free quota.

The deep-link path IS deliver_by_code, so gating it here proves there is no
deep-link bypass.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest_asyncio
from aiogram.types import User as TgUser
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.bot.delivery import DeliveryStatus, deliver_by_code
from app.core.tenant_context import all_tenants, tenant_scope
from app.models import (
    Admin,
    Base,
    Invoice,
    Media,
    MediaFile,
    MediaPurchase,
    Tenant,
    User,
    WalletTransaction,
)
from app.services.bot_setting_service import KEY_FREE_DAILY_QUOTA, BotSettingService
from app.services.paywall_service import (
    ALREADY,
    INSUFFICIENT,
    PURCHASED,
    PaywallService,
)
from app.services.wallet_service import WalletService

T = 2


@pytest_asyncio.fixture
async def sm():
    engine = create_async_engine(
        "sqlite+aiosqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    with all_tenants():
        async with Session() as s:
            s.add(Tenant(id=T, bot_username="a", bot_id=2002, status="active"))
            await s.commit()
    try:
        yield Session
    finally:
        await engine.dispose()


async def _media(sm, **kw):
    with tenant_scope(T):
        async with sm() as s:
            m = Media(code=kw.pop("code", "PW1"), status="approved", **kw)
            s.add(m)
            await s.flush()
            s.add(MediaFile(media_id=m.id, file_type="document", telegram_file_id="f"))
            await s.commit()
            return m.id


async def _user(sm, tg, balance=0, plan=None):
    with tenant_scope(T):
        async with sm() as s:
            u = User(telegram_id=tg, plan=plan)
            s.add(u)
            await s.commit()
            if balance:
                await WalletService(s).credit(u.id, balance, reference="seed")
            return u.id


async def _deliver(sm, tg, code):
    with tenant_scope(T):
        async with sm() as s:
            return await deliver_by_code(
                AsyncMock(), s, chat_id=tg,
                user=TgUser(id=tg, is_bot=False, first_name="x"), code=code,
            )


# --- plan gate ---------------------------------------------------------------
async def test_plan_gate_blocks_lower_allows_entitled_and_admin(sm):
    mid = await _media(sm, required_plan="plus")
    await _user(sm, 9001)                 # free user
    await _user(sm, 9002, plan="plus")    # entitled user
    with tenant_scope(T):
        async with sm() as s:
            s.add(User(telegram_id=9003))                       # admin's user row
            s.add(Admin(telegram_id=9003, role="owner", is_active=True))
            await s.commit()

    assert (await _deliver(sm, 9001, "PW1")).status is DeliveryStatus.PLAN_REQUIRED
    assert (await _deliver(sm, 9002, "PW1")).status is DeliveryStatus.DELIVERED
    assert (await _deliver(sm, 9003, "PW1")).status is DeliveryStatus.DELIVERED  # admin bypass


# --- paid file ----------------------------------------------------------------
async def test_paid_file_requires_settled_charge_exactly_once(sm):
    mid = await _media(sm, code="PAID", price=5000)
    uid = await _user(sm, 9101, balance=20000)

    # no entitlement -> deep link refuses with PAYMENT_REQUIRED
    assert (await _deliver(sm, 9101, "PAID")).status is DeliveryStatus.PAYMENT_REQUIRED

    with tenant_scope(T):
        async with sm() as s:
            media = await s.get(Media, mid)
            user = await s.get(User, uid)
            assert await PaywallService(s).purchase(media, user) == PURCHASED
        # a second buy folds into the entitlement — never a second charge
        async with sm() as s:
            media = await s.get(Media, mid)
            user = await s.get(User, uid)
            assert await PaywallService(s).purchase(media, user) == ALREADY
        async with sm() as s:
            assert await WalletService(s).balance(uid) == 15000  # charged ONCE
            n_ent = int(await s.scalar(select(func.count(MediaPurchase.id))))
            n_tx = int(await s.scalar(
                select(func.count(WalletTransaction.id)).where(
                    WalletTransaction.reference == f"media:{mid}:user:{uid}"
                )
            ))
            inv = await s.scalar(select(Invoice).where(Invoice.kind == "media"))
    assert n_ent == 1 and n_tx == 1
    assert inv is not None and inv.amount == 5000  # exactly one invoice

    # entitled now -> the same deep link delivers
    assert (await _deliver(sm, 9101, "PAID")).status is DeliveryStatus.DELIVERED


async def test_insufficient_funds_no_entitlement_no_charge(sm):
    mid = await _media(sm, code="RICH", price=9999)
    uid = await _user(sm, 9201, balance=10)
    with tenant_scope(T):
        async with sm() as s:
            media = await s.get(Media, mid)
            user = await s.get(User, uid)
            assert await PaywallService(s).purchase(media, user) == INSUFFICIENT
        async with sm() as s:
            assert await WalletService(s).balance(uid) == 10  # untouched
            assert int(await s.scalar(select(func.count(MediaPurchase.id)))) == 0
    assert (await _deliver(sm, 9201, "RICH")).status is DeliveryStatus.PAYMENT_REQUIRED


# --- free daily quota ----------------------------------------------------------
async def test_free_quota_decrements_and_blocks(sm):
    await _media(sm, code="Q1")
    await _media(sm, code="Q2")
    await _media(sm, code="Q3")
    await _user(sm, 9301)  # free user
    with tenant_scope(T):
        async with sm() as s:
            await BotSettingService(s).set(KEY_FREE_DAILY_QUOTA, 2)

    assert (await _deliver(sm, 9301, "Q1")).status is DeliveryStatus.DELIVERED
    assert (await _deliver(sm, 9301, "Q2")).status is DeliveryStatus.DELIVERED
    assert (await _deliver(sm, 9301, "Q3")).status is DeliveryStatus.QUOTA_EXCEEDED

    # a paid-plan user is never quota-limited
    await _user(sm, 9302, plan="plus")
    assert (await _deliver(sm, 9302, "Q1")).status is DeliveryStatus.DELIVERED
