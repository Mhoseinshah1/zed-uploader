"""Phase 5 unit tests: CentralPay start payload + disabled-hides-option.

SQLite + mocked HTTP (no network).
"""
from __future__ import annotations

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.bot import messages
from app.bot.keyboards.inline import build_topup_methods
from app.core.config import settings
from app.models import Base, User
from app.services import centralpay_service
from app.services.centralpay_service import CentralPayService


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


async def test_start_posts_payload_with_return_url(sqlite_maker, monkeypatch):
    captured: dict = {}

    async def fake_post(url, payload, timeout=20.0):
        captured["url"] = url
        captured["payload"] = payload
        return {"success": True, "data": {"redirectUrl": "https://redirect.example/x"}}

    monkeypatch.setattr(centralpay_service, "post_json", fake_post)

    async with sqlite_maker() as s:
        user = User(telegram_id=42)
        s.add(user)
        await s.commit()
        started = await CentralPayService(s).start(user, 5000, intent="topup")

    assert started is not None
    order_id, redirect = started
    assert redirect == "https://redirect.example/x"
    assert "getLink" in captured["url"]
    p = captured["payload"]
    assert p["type"] == "deposit"
    assert p["amount"] == 5000
    assert p["userId"] == user.id
    assert p["orderId"] == order_id
    assert f"orderId={order_id}" in p["returnUrl"]  # orderId embedded for the GET return


async def test_pay_return_route_reachable(monkeypatch):
    """GET /pay/centralpay/return?orderId=0 reaches the app (no auth/API key)."""
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
            resp = await client.get("/pay/centralpay/return?orderId=0")
        assert resp.status_code == 200
        assert "پرداخت ناموفق" in resp.text  # unknown order -> failed page
        assert "بازگشت به ربات" in resp.text
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


def test_disabled_hides_online_option():
    # conftest sets no CentralPay keys -> gateway disabled
    assert settings.centralpay_enabled is False
    disabled = [b.text for row in build_topup_methods(False).inline_keyboard for b in row]
    assert messages.BTN_PAY_CARD in disabled
    assert messages.BTN_PAY_ONLINE not in disabled
    enabled = [b.text for row in build_topup_methods(True).inline_keyboard for b in row]
    assert messages.BTN_PAY_ONLINE in enabled
