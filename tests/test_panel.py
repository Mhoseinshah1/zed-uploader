"""Phase 4 panel tests — bcrypt, auth redirect, CSRF, login lockout, idempotent
payment approval via the panel route. Offline (SQLite + fakeredis)."""
from __future__ import annotations

import asyncio

import httpx
from httpx import ASGITransport
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.redis_client import get_redis
from app.db.session import get_session
from app.models import Base, PanelUser, Payment, User, WalletTransaction
from app.panel import security, texts
from app.panel.security import hash_password, verify_password
from app.panel.session import COOKIE_NAME, SessionStore
from app.services.wallet_service import WalletService

PANEL = settings.panel_path


# --------------------------------------------------------------------------
# bcrypt roundtrip (no DB)
# --------------------------------------------------------------------------
def test_bcrypt_roundtrip():
    h = hash_password("S3cret-pass!")
    assert h != "S3cret-pass!"
    assert h.startswith("$2")  # bcrypt marker
    assert verify_password("S3cret-pass!", h) is True
    assert verify_password("wrong", h) is False


# --------------------------------------------------------------------------
# unauthenticated page redirects to login (uses the real app via TestClient)
# --------------------------------------------------------------------------
def test_unauthenticated_redirects(client):
    resp = client.get(PANEL, follow_redirects=False)
    assert resp.status_code == 302
    assert "/panel/login" in resp.headers["location"]


# --------------------------------------------------------------------------
# shared async harness (SQLite-backed app for authenticated flows)
# --------------------------------------------------------------------------
async def _make_client():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    from app.api.main import app

    async def _override():
        async with Session() as session:
            yield session

    app.dependency_overrides[get_session] = _override
    client = httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    )
    return app, engine, Session, client


async def _auth_cookie(uid: int) -> tuple[str, str]:
    csrf = security.generate_csrf()
    sid = await SessionStore(get_redis()).create({"uid": uid, "csrf": csrf})
    return security.sign(sid), csrf


# --------------------------------------------------------------------------
# CSRF: an authenticated POST without a token is 403
# --------------------------------------------------------------------------
async def _csrf() -> None:
    app, engine, Session, client = await _make_client()
    try:
        async with Session() as s:
            pu = PanelUser(username="admin", password_hash=hash_password("pw"))
            s.add(pu)
            await s.commit()
            uid = pu.id
        cookie, _csrf = await _auth_cookie(uid)
        client.cookies.set(COOKIE_NAME, cookie)
        resp = await client.post(
            f"{PANEL}/settings/card", data={"card_number": "123"}
        )
        assert resp.status_code == 403
    finally:
        await client.aclose()
        app.dependency_overrides.clear()
        await engine.dispose()


def test_csrf_required():
    asyncio.run(_csrf())


# --------------------------------------------------------------------------
# login rate limit locks after N failures
# --------------------------------------------------------------------------
async def _lockout() -> None:
    app, engine, Session, client = await _make_client()
    try:
        await client.get(f"{PANEL}/login")  # sets pre-auth csrf cookie
        token = security.unsign(client.cookies.get("zpcsrf"))
        last = None
        for _ in range(security.LOGIN_MAX_FAILURES + 1):
            last = await client.post(
                f"{PANEL}/login",
                data={
                    "username": "ratelimit_probe",
                    "password": "wrong",
                    "csrf_token": token,
                },
            )
        assert last is not None and last.status_code == 200
        assert texts.LOGIN_LOCKED in last.text
    finally:
        await client.aclose()
        app.dependency_overrides.clear()
        await engine.dispose()


def test_login_lockout():
    asyncio.run(_lockout())


# --------------------------------------------------------------------------
# approving a payment via the panel credits exactly once (idempotent)
# --------------------------------------------------------------------------
class _FakeBot:
    def __init__(self) -> None:
        self.sent: list = []

    async def send_message(self, chat_id, text):
        self.sent.append((chat_id, text))


async def _payment_idempotent() -> None:
    app, engine, Session, client = await _make_client()
    app.state.bot = _FakeBot()
    try:
        async with Session() as s:
            pu = PanelUser(username="admin2", password_hash=hash_password("pw"))
            user = User(telegram_id=5000)
            s.add_all([pu, user])
            await s.commit()
            pay = Payment(user_id=user.id, amount=700, method="card", status="pending")
            s.add(pay)
            await s.commit()
            uid, user_id, pay_id = pu.id, user.id, pay.id

        cookie, csrf = await _auth_cookie(uid)
        client.cookies.set(COOKIE_NAME, cookie)
        for _ in range(2):
            resp = await client.post(
                f"{PANEL}/payments/{pay_id}/approve",
                data={"csrf_token": csrf},
                follow_redirects=False,
            )
            assert resp.status_code == 302

        async with Session() as s:
            balance = await WalletService(s).balance(user_id)
            deposits = int(
                await s.scalar(
                    select(func.count(WalletTransaction.id)).where(
                        WalletTransaction.user_id == user_id,
                        WalletTransaction.type == "deposit",
                    )
                )
            )
        assert balance == 700
        assert deposits == 1
        assert len(app.state.bot.sent) == 1  # user notified exactly once
    finally:
        await client.aclose()
        app.dependency_overrides.clear()
        await engine.dispose()


def test_panel_payment_approval_idempotent():
    asyncio.run(_payment_idempotent())
