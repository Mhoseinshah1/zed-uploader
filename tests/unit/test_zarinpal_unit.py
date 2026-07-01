"""C1 unit tests — Zarinpal create payload, registry enablement, generic route.

SQLite + mocked HTTP (no network).
"""
from __future__ import annotations

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.models import Base, User
from app.services.gateway_service import GatewayService
from app.services.providers import enabled_providers, upsert_config
from app.services.providers import zarinpal as zarinpal_module
from app.services.providers.zarinpal import ZarinpalProvider


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


async def test_create_builds_payload_and_persists_authority(sqlite_maker, monkeypatch):
    captured: dict = {}

    async def fake_post(url, payload, timeout=20.0):
        captured["url"] = url
        captured["payload"] = payload
        return {"Status": 100, "Authority": "A0000012345"}

    monkeypatch.setattr(zarinpal_module, "post_json", fake_post)

    async with sqlite_maker() as s:
        user = User(telegram_id=77)
        s.add(user)
        await s.commit()
        gateway = GatewayService(s, ZarinpalProvider("MERCHANT-UUID"))
        started = await gateway.start(user, 12000, intent="topup")

        assert started is not None
        order_id, redirect = started
        # redirect goes to StartPay with the gateway's authority
        assert redirect == "https://www.zarinpal.com/pg/StartPay/A0000012345"
        # request payload per the legacy REST API
        assert captured["url"].endswith("/pg/rest/WebGate/PaymentRequest.json")
        p = captured["payload"]
        assert p["MerchantID"] == "MERCHANT-UUID"
        assert p["Amount"] == 12000
        assert p["Description"]
        assert f"/pay/zarinpal/return?orderId={order_id}" in p["CallbackURL"]

        # the authority is persisted on the payment row for the GET return
        from app.models import Payment

        payment = await s.get(Payment, order_id)
        assert payment.authority == "A0000012345"
        assert payment.method == "zarinpal" and payment.provider == "zarinpal"


async def test_sandbox_uses_sandbox_urls(sqlite_maker, monkeypatch):
    captured: dict = {}

    async def fake_post(url, payload, timeout=20.0):
        captured["url"] = url
        return {"Status": 100, "Authority": "SBX1"}

    monkeypatch.setattr(zarinpal_module, "post_json", fake_post)

    async with sqlite_maker() as s:
        user = User(telegram_id=78)
        s.add(user)
        await s.commit()
        started = await GatewayService(
            s, ZarinpalProvider("M", sandbox=True)
        ).start(user, 5000, intent="topup")

    assert started is not None
    assert started[1] == "https://sandbox.zarinpal.com/pg/StartPay/SBX1"
    assert captured["url"].startswith("https://sandbox.zarinpal.com/")


async def test_declined_request_returns_none(sqlite_maker, monkeypatch):
    async def fake_post(url, payload, timeout=20.0):
        return {"Status": -9, "Authority": ""}

    monkeypatch.setattr(zarinpal_module, "post_json", fake_post)
    async with sqlite_maker() as s:
        user = User(telegram_id=79)
        s.add(user)
        await s.commit()
        assert await GatewayService(s, ZarinpalProvider("M")).start(
            user, 5000, intent="topup"
        ) is None


# --- registry enablement ----------------------------------------------------
async def test_enabled_providers_gating(sqlite_maker):
    async with sqlite_maker() as s:
        # nothing configured: centralpay needs env keys (absent in tests),
        # zarinpal needs a row -> nothing enabled, online option hidden
        assert await enabled_providers(s) == []

        # a row without a merchant id is still not offered
        await upsert_config(s, "zarinpal", is_enabled=True)
        assert await enabled_providers(s) == []

        # merchant set + enabled -> offered
        await upsert_config(s, "zarinpal", merchant_id="M-1")
        assert await enabled_providers(s) == ["zarinpal"]

        # owner switches it off -> hidden again
        await upsert_config(s, "zarinpal", is_enabled=False)
        assert await enabled_providers(s) == []


async def test_generic_return_route_serves_all_providers():
    """GET /pay/{provider}/return is reachable for zarinpal and rejects unknowns."""
    import httpx
    from httpx import ASGITransport

    from app.api.main import app
    from app.db.session import get_session

    engine = create_async_engine(
        "sqlite+aiosqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _override():
        async with maker() as s:
            yield s

    app.dependency_overrides[get_session] = _override
    try:
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/pay/zarinpal/return?orderId=0")
            assert resp.status_code == 200 and "پرداخت ناموفق" in resp.text
            resp = await client.get("/pay/unknowngw/return?orderId=1")
            assert resp.status_code == 200 and "پرداخت ناموفق" in resp.text
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()
