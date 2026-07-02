"""E2 tests — activation server: seats, idempotency, revocation, /check, and
JSON interop with E1's LicenseService client. SQLite + ASGI; no network."""
from __future__ import annotations

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from activation_server.main import create_app
from activation_server.store import ActivationStore


@pytest_asyncio.fixture
async def server(tmp_path):
    """(client, store) against a fresh ASGI activation app + tmp SQLite."""
    db = str(tmp_path / "activation.db")
    app = create_app(db)
    store = ActivationStore(db)
    await store.init()
    app.state.store = store  # lifespan does not run under bare ASGITransport
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://act")
    try:
        yield client, store
    finally:
        await client.aclose()
        await store.close()


async def test_seat_limit_and_idempotent_reactivation(server):
    client, store = server
    await store.issue("KEY-2SEAT", seats=2, days=365)

    r1 = (await client.post("/activate", json={"key": "KEY-2SEAT", "fingerprint": "fp1"})).json()
    assert r1["ok"] is True and r1["status"] == "active" and r1["seats_used"] == 1
    assert r1["allowed_install_count"] == 2 and r1["expires_at"]

    # re-activating the SAME fingerprint is idempotent — no extra seat
    r1b = (await client.post("/activate", json={"key": "KEY-2SEAT", "fingerprint": "fp1"})).json()
    assert r1b["ok"] is True and r1b["seats_used"] == 1

    r2 = (await client.post("/activate", json={"key": "KEY-2SEAT", "fingerprint": "fp2"})).json()
    assert r2["ok"] is True and r2["seats_used"] == 2

    # a third fingerprint exceeds the limit -> refused
    r3 = (await client.post("/activate", json={"key": "KEY-2SEAT", "fingerprint": "fp3"})).json()
    assert r3["ok"] is False and r3["status"] == "seat_limit" and r3["seats_used"] == 2


async def test_revoked_and_unknown_and_expired(server):
    client, store = server
    await store.issue("KEY-R", seats=1, days=None)
    await store.revoke("KEY-R")
    r = (await client.post("/activate", json={"key": "KEY-R", "fingerprint": "f"})).json()
    assert r["ok"] is False and r["status"] == "revoked"
    r = (await client.post("/check", json={"key": "KEY-R", "fingerprint": "f"})).json()
    assert r["ok"] is False and r["status"] == "revoked"

    r = (await client.post("/check", json={"key": "NOPE", "fingerprint": "f"})).json()
    assert r["ok"] is False and r["status"] == "unknown"

    await store.issue("KEY-E", seats=1, days=-1)  # already expired
    r = (await client.post("/activate", json={"key": "KEY-E", "fingerprint": "f"})).json()
    assert r["ok"] is False and r["status"] == "expired"


async def test_check_statuses(server):
    client, store = server
    await store.issue("KEY-C", seats=1, days=30)
    # bound fingerprint -> active; unbound -> unknown (never consumes a seat)
    await client.post("/activate", json={"key": "KEY-C", "fingerprint": "bound"})
    r = (await client.post("/check", json={"key": "KEY-C", "fingerprint": "bound"})).json()
    assert r["ok"] is True and r["status"] == "active"
    r = (await client.post("/check", json={"key": "KEY-C", "fingerprint": "other"})).json()
    assert r["ok"] is False and r["status"] == "unknown"
    assert r["seats_used"] == 1  # the unbound check did not take a seat


async def test_interop_with_e1_license_service(server, monkeypatch, tmp_path):
    """E1's LicenseService speaks to the REAL activation app end-to-end."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import StaticPool

    import app.services.license_service as license_service
    from app.core.config import settings
    from app.models import Base
    from app.services.license_service import LicenseService, paid_features_allowed

    client, store = server
    await store.issue("KEY-INTEROP", seats=1, days=365)

    monkeypatch.setattr(settings, "license_disabled", False)
    monkeypatch.setattr(settings, "license_key", "KEY-INTEROP")
    monkeypatch.setattr(settings, "license_server_url", "http://act")
    monkeypatch.setattr(settings, "license_file", str(tmp_path / "license.json"))

    async def post_via_asgi(url, payload, timeout=20.0):
        path = url.replace("http://act", "")
        resp = await client.post(path, json=payload)
        return resp.json()

    monkeypatch.setattr(license_service, "post_json", post_via_asgi)

    engine = create_async_engine(
        "sqlite+aiosqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with maker() as s:
            assert await LicenseService(s).activate("KEY-INTEROP") == "ok"
            assert await paid_features_allowed(s) is True
            row = await LicenseService(s).get_row()
            assert row.status == "active" and row.allowed_install_count == 1
            # periodic re-check agrees
            assert await LicenseService(s).check() == "ok"
            # seller revokes -> next check degrades paid features
            await store.revoke("KEY-INTEROP")
            assert await LicenseService(s).check() == "rejected"
            assert await paid_features_allowed(s) is False
    finally:
        await engine.dispose()


async def test_main_app_tolerates_absent_server(monkeypatch, tmp_path):
    """Licensing on + unreachable server -> offline grace, never a crash."""
    from datetime import datetime, timedelta, timezone

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import StaticPool

    import app.services.license_service as license_service
    from app.core.config import settings
    from app.models import Base
    from app.services.license_service import LicenseService, paid_features_allowed

    monkeypatch.setattr(settings, "license_disabled", False)
    monkeypatch.setattr(settings, "license_key", "K")
    monkeypatch.setattr(settings, "license_server_url", "http://127.0.0.1:1")
    monkeypatch.setattr(settings, "license_file", str(tmp_path / "license.json"))

    async def dead_server(url, payload, timeout=20.0):
        return {}  # what post_json returns on any network failure

    monkeypatch.setattr(license_service, "post_json", dead_server)

    engine = create_async_engine(
        "sqlite+aiosqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with maker() as s:
            assert await LicenseService(s).check() == "offline"
            # previously-active install inside the grace window stays allowed
            row = await LicenseService(s).get_row()
            row.status = "active"
            row.last_ok_at = datetime.now(timezone.utc) - timedelta(days=2)
            await s.commit()
            assert await paid_features_allowed(s) is True
    finally:
        await engine.dispose()
