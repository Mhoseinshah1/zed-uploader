"""C1b unit tests — Zibal payload (Toman->Rial), readiness gating, per-provider
config isolation, CentralPay env-fallback vs panel-config keys.

SQLite + mocked HTTP (no network).
"""
from __future__ import annotations

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.models import Base, User
from app.services import centralpay_service
from app.services.gateway_service import GatewayService
from app.services.providers import (
    STATUS_DISABLED,
    STATUS_READY,
    STATUS_UNCONFIGURED,
    enabled_providers,
    get_config,
    get_provider,
    provider_status,
    upsert_config,
)
from app.services.providers import zibal as zibal_module
from app.services.providers.zibal import ZibalProvider


@pytest_asyncio.fixture
async def sqlite_maker():
    engine = create_async_engine(
        "sqlite+aiosqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


# --- create payload ---------------------------------------------------------
async def test_create_builds_payload_rial_and_persists_trackid(sqlite_maker, monkeypatch):
    captured: dict = {}

    async def fake_post(url, payload, timeout=20.0):
        captured["url"] = url
        captured["payload"] = payload
        return {"result": 100, "trackId": 998877}

    monkeypatch.setattr(zibal_module, "post_json", fake_post)

    async with sqlite_maker() as s:
        user = User(telegram_id=91)
        s.add(user)
        await s.commit()
        started = await GatewayService(s, ZibalProvider("MY-MERCHANT")).start(
            user, 12000, intent="topup"  # 12000 Toman
        )

        assert started is not None
        order_id, redirect = started
        assert redirect == "https://gateway.zibal.ir/start/998877"
        assert captured["url"] == "https://gateway.zibal.ir/v1/request"
        p = captured["payload"]
        assert p["merchant"] == "MY-MERCHANT"
        assert p["amount"] == 120000  # Toman -> Rial (x10)
        assert f"/pay/zibal/return?orderId={order_id}" in p["callbackUrl"]

        from app.models import Payment

        payment = await s.get(Payment, order_id)
        assert payment.authority == "998877"  # trackId stored for the GET return
        assert payment.method == "zibal" and payment.provider == "zibal"


async def test_sandbox_uses_test_merchant(sqlite_maker, monkeypatch):
    captured: dict = {}

    async def fake_post(url, payload, timeout=20.0):
        captured["payload"] = payload
        return {"result": 100, "trackId": 1}

    monkeypatch.setattr(zibal_module, "post_json", fake_post)
    async with sqlite_maker() as s:
        user = User(telegram_id=92)
        s.add(user)
        await s.commit()
        started = await GatewayService(
            s, ZibalProvider("ignored", sandbox=True)
        ).start(user, 500, intent="topup")
    assert started is not None
    assert captured["payload"]["merchant"] == "zibal"  # Zibal's public test merchant


async def test_declined_request_returns_none(sqlite_maker, monkeypatch):
    async def fake_post(url, payload, timeout=20.0):
        return {"result": 102, "message": "merchant not found"}

    monkeypatch.setattr(zibal_module, "post_json", fake_post)
    async with sqlite_maker() as s:
        user = User(telegram_id=93)
        s.add(user)
        await s.commit()
        assert await GatewayService(s, ZibalProvider("M")).start(
            user, 500, intent="topup"
        ) is None


# --- readiness gating ---------------------------------------------------------
async def test_enabled_but_unconfigured_not_offered(sqlite_maker):
    async with sqlite_maker() as s:
        # zibal enabled but no merchant and no sandbox -> NOT offered
        await upsert_config(s, "zibal", is_enabled=True)
        assert "zibal" not in await enabled_providers(s)
        assert await provider_status(s, "zibal") == STATUS_UNCONFIGURED

        # sandbox counts as configured (test merchant)
        await upsert_config(s, "zibal", sandbox=True)
        assert "zibal" in await enabled_providers(s)
        assert await provider_status(s, "zibal") == STATUS_READY

        # a real merchant works too
        await upsert_config(s, "zibal", sandbox=False, config={"merchant": "M-9"})
        assert "zibal" in await enabled_providers(s)

        # kill switch
        await upsert_config(s, "zibal", is_enabled=False)
        assert "zibal" not in await enabled_providers(s)
        assert await provider_status(s, "zibal") == STATUS_DISABLED


async def test_centralpay_ready_from_config_row_without_env(sqlite_maker, monkeypatch):
    # no env keys (test conftest leaves them empty)
    assert settings.centralpay_enabled is False
    captured: dict = {}

    async def fake_post(url, payload, timeout=20.0):
        captured["payload"] = payload
        return {"success": True, "data": {"redirectUrl": "https://r/x"}}

    monkeypatch.setattr(centralpay_service, "post_json", fake_post)

    async with sqlite_maker() as s:
        await upsert_config(
            s, "centralpay", is_enabled=True,
            config={"getlink_key": "ROW-GETLINK", "verify_key": "ROW-VERIFY"},
        )
        assert "centralpay" in await enabled_providers(s)
        assert await provider_status(s, "centralpay") == STATUS_READY

        provider = await get_provider(s, "centralpay")
        user = User(telegram_id=94)
        s.add(user)
        await s.commit()
        started = await GatewayService(s, provider).start(user, 700, intent="topup")
    assert started is not None
    assert captured["payload"]["api_key"] == "ROW-GETLINK"  # row key, not env


async def test_centralpay_env_fallback_still_works(sqlite_maker, monkeypatch):
    monkeypatch.setattr(settings, "centralpay_getlink_key", "ENV-GETLINK")
    monkeypatch.setattr(settings, "centralpay_verify_key", "ENV-VERIFY")
    captured: dict = {}

    async def fake_post(url, payload, timeout=20.0):
        captured["payload"] = payload
        return {"success": True, "data": {"redirectUrl": "https://r/y"}}

    monkeypatch.setattr(centralpay_service, "post_json", fake_post)

    async with sqlite_maker() as s:
        # no config row at all: env keys alone make it ready (pre-C1b behavior)
        assert "centralpay" in await enabled_providers(s)
        provider = await get_provider(s, "centralpay")
        user = User(telegram_id=95)
        s.add(user)
        await s.commit()
        started = await GatewayService(s, provider).start(user, 800, intent="topup")
    assert started is not None
    assert captured["payload"]["api_key"] == "ENV-GETLINK"


# --- per-provider save isolation ---------------------------------------------
async def test_saving_one_provider_does_not_touch_others(sqlite_maker):
    async with sqlite_maker() as s:
        await upsert_config(
            s, "zarinpal", is_enabled=True, config={"merchant_id": "ZP-1"}
        )
        await upsert_config(s, "zibal", is_enabled=True, config={"merchant": "ZB-1"})

        # editing zibal only
        await upsert_config(s, "zibal", is_enabled=False, config={"merchant": "ZB-2"})

        zp = await get_config(s, "zarinpal")
        zb = await get_config(s, "zibal")
        assert zp.is_enabled is True and zp.config == {"merchant_id": "ZP-1"}
        assert zb.is_enabled is False and zb.config == {"merchant": "ZB-2"}

        # blank secret input = keep the existing value (write-only masking)
        await upsert_config(s, "zibal", config={"merchant": ""})
        zb = await get_config(s, "zibal")
        assert zb.config == {"merchant": "ZB-2"}
