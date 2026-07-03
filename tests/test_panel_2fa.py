"""J9 — panel account security: stdlib TOTP, optional 2FA login step,
password change, epoch-based logout-all, owner recovery."""
from __future__ import annotations

import re
import time

import httpx
import pytest_asyncio
from httpx import ASGITransport
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.crypto import decrypt_secret, encrypt_secret
from app.core.redis_client import get_redis
from app.core.tenant_context import all_tenants
from app.db.session import get_session
from app.models import Base, PanelUser, Tenant
from app.panel import security
from app.panel.security import hash_password, verify_password
from app.panel.session import COOKIE_NAME, SessionStore
from app.panel.totp import generate_secret, provisioning_uri, totp_at, verify_totp

PANEL = settings.panel_path
RFC_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # b32("12345678901234567890")


# --- TOTP unit (RFC 6238 SHA-1 vectors, 6 digits) -------------------------------
def test_totp_rfc6238_vectors():
    assert totp_at(RFC_SECRET, 59) == "287082"
    assert totp_at(RFC_SECRET, 1111111109) == "081804"
    assert totp_at(RFC_SECRET, 1111111111) == "050471"
    assert totp_at(RFC_SECRET, 1234567890) == "005924"


def test_verify_totp_window_and_rejects():
    secret = generate_secret()
    assert re.fullmatch(r"[A-Z2-7]{32}", secret)  # 160-bit base32
    now = time.time()
    assert verify_totp(secret, totp_at(secret, now))
    assert verify_totp(secret, totp_at(secret, now - 30))   # ±1 step drift ok
    assert not verify_totp(secret, totp_at(secret, now - 300))  # stale
    assert not verify_totp(secret, "000000") or totp_at(secret, now) == "000000"
    assert not verify_totp(secret, "12345")   # wrong length
    assert not verify_totp(secret, "abcdef")  # not digits
    assert not verify_totp(secret, "")


def test_provisioning_uri_contents():
    uri = provisioning_uri(RFC_SECRET, "boss")
    assert uri.startswith("otpauth://totp/")
    assert f"secret={RFC_SECRET}" in uri and "issuer=ZedUploader" in uri and "boss" in uri


# --- HTTP harness ----------------------------------------------------------------
@pytest_asyncio.fixture
async def env():
    engine = create_async_engine(
        "sqlite+aiosqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    ids = {}
    with all_tenants():
        async with Session() as s:
            s.add(Tenant(id=2, bot_username="a", bot_id=2002, status="active"))
            boss = PanelUser(username="j9_boss", password_hash=hash_password("Bosspass1"),
                             tenant_id=2, role="owner", is_superadmin=False)
            staff = PanelUser(username="j9_staff", password_hash=hash_password("Staffpass1"),
                              tenant_id=2, role="support", is_superadmin=False)
            s.add_all([boss, staff])
            await s.commit()
            ids["boss"], ids["staff"] = boss.id, staff.id
    from app.api.main import app

    async def _override():
        async with Session() as s:
            yield s

    app.dependency_overrides[get_session] = _override
    try:
        yield app, Session, ids
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


async def _authed(app, Session, uid):
    """Client with a live (epoch-stamped) session for panel user ``uid``."""
    async with Session() as s:
        user = await s.get(PanelUser, uid)
        epoch = int(user.session_epoch or 0)
    csrf = security.generate_csrf()
    sid = await SessionStore(get_redis()).create(
        {"uid": uid, "csrf": csrf, "epoch": epoch}
    )
    client = httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t")
    client.cookies.set(COOKIE_NAME, security.sign(sid))
    return client, csrf


async def _login(client, username, password):
    await client.get(f"{PANEL}/login")  # sets the pre-auth csrf cookie
    token = security.unsign(client.cookies.get("zpcsrf"))
    return await client.post(
        f"{PANEL}/login",
        data={"username": username, "password": password, "csrf_token": token},
        follow_redirects=False,
    )


# --- login flows -------------------------------------------------------------------
async def test_login_without_2fa_unchanged(env):
    app, Session, ids = env
    client = httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t")
    try:
        resp = await _login(client, "j9_boss", "Bosspass1")
        assert resp.status_code == 302 and client.cookies.get(COOKIE_NAME)
        assert (await client.get(f"{PANEL}/account")).status_code == 200
    finally:
        await client.aclose()


async def test_login_with_2fa_requires_code(env):
    app, Session, ids = env
    secret = generate_secret()
    async with Session() as s:
        user = await s.get(PanelUser, ids["boss"])
        user.totp_secret = encrypt_secret(secret)
        user.twofa_enabled = True
        await s.commit()

    client = httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t")
    try:
        resp = await _login(client, "j9_boss", "Bosspass1")
        # password alone: NO session — the 2FA form + pending cookie instead
        assert resp.status_code == 200
        assert client.cookies.get(COOKIE_NAME) is None
        assert client.cookies.get("zp2fa")

        token = security.unsign(client.cookies.get("zpcsrf"))
        wrong = await client.post(
            f"{PANEL}/login/2fa",
            data={"code": "000000", "csrf_token": token}, follow_redirects=False,
        )
        assert wrong.status_code == 200 and client.cookies.get(COOKIE_NAME) is None

        good = await client.post(
            f"{PANEL}/login/2fa",
            data={"code": totp_at(secret, time.time()), "csrf_token": token},
            follow_redirects=False,
        )
        assert good.status_code == 302 and client.cookies.get(COOKIE_NAME)
        assert (await client.get(f"{PANEL}/account")).status_code == 200

        # the pending record was single-use: replaying the code page fails closed
        replay = await client.post(
            f"{PANEL}/login/2fa",
            data={"code": totp_at(secret, time.time()), "csrf_token": token},
            follow_redirects=False,
        )
        assert replay.status_code == 302
        assert f"{PANEL}/login" in replay.headers["location"]
    finally:
        await client.aclose()


# --- self-service 2FA setup ---------------------------------------------------------
async def test_account_2fa_setup_enable_disable(env):
    app, Session, ids = env
    client, csrf = await _authed(app, Session, ids["staff"])
    try:
        start = await client.post(
            f"{PANEL}/account/2fa/start", data={"csrf_token": csrf}
        )
        assert start.status_code == 200
        m = re.search(r'<code dir="ltr">([A-Z2-7]{32})</code>', start.text)
        assert m, "setup page must show the secret exactly once"
        secret = m.group(1)
        assert "otpauth://totp/" in start.text

        async with Session() as s:
            user = await s.get(PanelUser, ids["staff"])
            assert user.twofa_enabled is False           # not enabled until verified
            assert user.totp_secret != secret            # ciphertext at rest
            assert decrypt_secret(user.totp_secret) == secret

        resp = await client.post(
            f"{PANEL}/account/2fa/enable",
            data={"code": totp_at(secret, time.time()), "csrf_token": csrf},
            follow_redirects=False,
        )
        assert resp.status_code == 302 and "ok=enabled" in resp.headers["location"]
        async with Session() as s:
            assert (await s.get(PanelUser, ids["staff"])).twofa_enabled is True

        # disable requires the password; wrong one is refused
        bad = await client.post(
            f"{PANEL}/account/2fa/disable",
            data={"current_password": "nope", "csrf_token": csrf},
            follow_redirects=False,
        )
        assert "error=badpass" in bad.headers["location"]
        ok = await client.post(
            f"{PANEL}/account/2fa/disable",
            data={"current_password": "Staffpass1", "csrf_token": csrf},
            follow_redirects=False,
        )
        assert "ok=disabled" in ok.headers["location"]
        async with Session() as s:
            user = await s.get(PanelUser, ids["staff"])
            assert user.twofa_enabled is False and user.totp_secret is None
    finally:
        await client.aclose()


# --- password change + epoch logout --------------------------------------------------
async def test_own_password_change_kills_sessions(env):
    app, Session, ids = env
    client, csrf = await _authed(app, Session, ids["staff"])
    other, _ = await _authed(app, Session, ids["staff"])  # a second device
    try:
        bad = await client.post(
            f"{PANEL}/account/password",
            data={"current_password": "wrong", "new_password": "Newpass123",
                  "csrf_token": csrf},
            follow_redirects=False,
        )
        assert "error=badpass" in bad.headers["location"]

        resp = await client.post(
            f"{PANEL}/account/password",
            data={"current_password": "Staffpass1", "new_password": "Newpass123",
                  "csrf_token": csrf},
            follow_redirects=False,
        )
        assert resp.status_code == 302 and f"{PANEL}/login" in resp.headers["location"]
        async with Session() as s:
            user = await s.get(PanelUser, ids["staff"])
            assert verify_password("Newpass123", user.password_hash)
        # EVERY pre-change session is dead (epoch bump), not just this one
        r = await other.get(f"{PANEL}/account", follow_redirects=False)
        assert r.status_code == 302 and "login" in r.headers["location"]
    finally:
        await client.aclose()
        await other.aclose()


async def test_owner_logout_all_and_recovery(env):
    app, Session, ids = env
    staff_client, _ = await _authed(app, Session, ids["staff"])
    owner_client, ocsrf = await _authed(app, Session, ids["boss"])
    try:
        assert (await staff_client.get(f"{PANEL}/account")).status_code == 200

        # owner: logout-all for the member
        r = await owner_client.post(
            f"{PANEL}/team/{ids['staff']}/logout_all",
            data={"csrf_token": ocsrf}, follow_redirects=False,
        )
        assert r.status_code == 302
        dead = await staff_client.get(f"{PANEL}/account", follow_redirects=False)
        assert dead.status_code == 302 and "login" in dead.headers["location"]

        # owner: reset the member's password
        r = await owner_client.post(
            f"{PANEL}/team/{ids['staff']}/password",
            data={"password": "Resetpass1", "csrf_token": ocsrf},
            follow_redirects=False,
        )
        assert r.status_code == 302
        async with Session() as s:
            member = await s.get(PanelUser, ids["staff"])
            assert verify_password("Resetpass1", member.password_hash)

        # owner: 2FA recovery — turn a member's lost 2FA off
        secret = generate_secret()
        async with Session() as s:
            member = await s.get(PanelUser, ids["staff"])
            member.totp_secret = encrypt_secret(secret)
            member.twofa_enabled = True
            await s.commit()
        r = await owner_client.post(
            f"{PANEL}/team/{ids['staff']}/2fa/disable",
            data={"csrf_token": ocsrf}, follow_redirects=False,
        )
        assert r.status_code == 302
        async with Session() as s:
            member = await s.get(PanelUser, ids["staff"])
            assert member.twofa_enabled is False and member.totp_secret is None

        # and the member logs back in with the reset password, no code asked
        fresh = httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t")
        try:
            resp = await _login(fresh, "j9_staff", "Resetpass1")
            assert resp.status_code == 302 and fresh.cookies.get(COOKIE_NAME)
        finally:
            await fresh.aclose()
    finally:
        await staff_client.aclose()
        await owner_client.aclose()
