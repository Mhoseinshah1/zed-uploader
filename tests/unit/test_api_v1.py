"""D4 tests — v1 admin API: JWT auth, CRUD happy-paths, pagination/filtering,
secret hygiene, legacy X-API-Key compat.

SQLite + ASGI transport; no network.
"""
from __future__ import annotations

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core import jwt_utils
from app.db.session import get_session
from app.models import Base, Folder, Media, MediaFile, Payment, PanelUser, Plan, User
from app.panel import security
from app.services.wallet_service import WalletService


@pytest_asyncio.fixture
async def harness():
    engine = create_async_engine(
        "sqlite+aiosqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    from app.api.main import app

    async def _override():
        async with Session() as s:
            yield s

    app.dependency_overrides[get_session] = _override

    async with Session() as s:
        s.add(PanelUser(username="boss", password_hash=security.hash_password("pw12345"), tenant_id=1))
        await s.commit()

    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    try:
        resp = await client.post(
            "/api/v1/auth/login", json={"username": "boss", "password": "pw12345"}
        )
        token = resp.json()["access_token"]
        client.headers["Authorization"] = f"Bearer {token}"
        yield Session, client
    finally:
        await client.aclose()
        app.dependency_overrides.clear()
        await engine.dispose()


async def test_login_and_auth_required(harness):
    Session, client = harness
    # bad creds -> 401
    fresh = AsyncClient(
        transport=client._transport, base_url="http://test"
    )
    resp = await fresh.post(
        "/api/v1/auth/login", json={"username": "boss", "password": "nope"}
    )
    assert resp.status_code == 401
    # no token -> 401
    assert (await fresh.get("/api/v1/media")).status_code == 401
    # garbage token -> 401
    fresh.headers["Authorization"] = "Bearer not.a.jwt"
    assert (await fresh.get("/api/v1/media")).status_code == 401
    # forged/expired token -> 401
    expired = jwt_utils.encode(1, ttl=-10)
    fresh.headers["Authorization"] = f"Bearer {expired}"
    assert (await fresh.get("/api/v1/media")).status_code == 401
    await fresh.aclose()
    # valid token -> 200
    assert (await client.get("/api/v1/media")).status_code == 200


async def test_media_crud_pagination_and_secret_hygiene(harness):
    Session, client = harness
    async with Session() as s:
        for i in range(3):
            m = Media(code=f"apic{i}", status="approved", password_hash="bcrypt-secret")
            m.files.append(MediaFile(sort_order=0, telegram_file_id="f", file_type="document"))
            s.add(m)
        s.add(Folder(name="dst"))
        await s.commit()

    resp = await client.get("/api/v1/media?limit=2&offset=0")
    data = resp.json()
    assert data["total"] == 3 and len(data["items"]) == 2
    assert "password_hash" not in resp.text  # secrets never serialized
    assert data["items"][0]["has_password"] is True

    # filter by code substring
    data = (await client.get("/api/v1/media?q=apic1")).json()
    assert data["total"] == 1 and data["items"][0]["code"] == "apic1"

    # patch: deactivate + move to folder
    mid = data["items"][0]["id"]
    resp = await client.patch(
        f"/api/v1/media/{mid}", json={"is_active": False, "folder_id": 1}
    )
    body = resp.json()
    assert body["is_active"] is False and body["folder_id"] == 1
    # bad folder -> 400
    assert (
        await client.patch(f"/api/v1/media/{mid}", json={"folder_id": 999})
    ).status_code == 400

    # delete
    assert (await client.delete(f"/api/v1/media/{mid}")).status_code == 200
    assert (await client.get("/api/v1/media")).json()["total"] == 2


async def test_users_list_filter_and_block(harness):
    Session, client = harness
    async with Session() as s:
        s.add_all([
            User(telegram_id=1, username="alpha"),
            User(telegram_id=2, username="beta", is_blocked=True),
        ])
        await s.commit()

    data = (await client.get("/api/v1/users?blocked=true")).json()
    assert data["total"] == 1 and data["items"][0]["username"] == "beta"

    uid = (await client.get("/api/v1/users?q=alpha")).json()["items"][0]["id"]
    body = (await client.patch(f"/api/v1/users/{uid}", json={"is_blocked": True})).json()
    assert body["is_blocked"] is True


async def test_plans_ads_channels_folders_crud(harness):
    Session, client = harness
    async with Session() as s:
        s.add(Plan(key="plus", title="Plus", price=100, duration_days=30, max_files=10))
        await s.commit()

    body = (await client.patch(
        "/api/v1/plans/plus", json={"price": 555, "stars_price": 9}
    )).json()
    assert body["price"] == 555

    ad_id = (await client.post("/api/v1/ads", json={
        "title": "t", "text": "x", "placement": "before_file"
    })).json()["id"]
    assert (await client.get("/api/v1/ads")).json()["total"] == 1
    assert (await client.delete(f"/api/v1/ads/{ad_id}")).status_code == 200

    ch = (await client.post("/api/v1/channels", json={"chat_id": "@chan"})).json()
    assert (await client.get("/api/v1/channels")).json()["items"][0]["chat_id"] == "@chan"
    assert (await client.delete(f"/api/v1/channels/{ch['id']}")).status_code == 200

    folder = (await client.post("/api/v1/folders", json={"name": "root"})).json()
    sub = (await client.post(
        "/api/v1/folders", json={"name": "sub", "parent_id": folder["id"]}
    )).json()
    # deleting a parent with children -> 409
    assert (await client.delete(f"/api/v1/folders/{folder['id']}")).status_code == 409
    assert (await client.delete(f"/api/v1/folders/{sub['id']}")).status_code == 200


async def test_payments_read_and_card_only_approve(harness):
    Session, client = harness
    async with Session() as s:
        user = User(telegram_id=9)
        s.add(user)
        await s.commit()
        s.add_all([
            Payment(user_id=user.id, amount=1000, method="card", status="pending"),
            Payment(user_id=user.id, amount=2000, method="zibal", status="pending"),
        ])
        await s.commit()
        uid = user.id

    pending = (await client.get("/api/v1/payments?status=pending")).json()
    assert pending["total"] == 2
    card = next(p for p in pending["items"] if p["method"] == "card")
    online = next(p for p in pending["items"] if p["method"] == "zibal")

    # gateway payments cannot be manually approved
    assert (await client.post(f"/api/v1/payments/{online['id']}/approve")).status_code == 400

    body = (await client.post(f"/api/v1/payments/{card['id']}/approve")).json()
    assert body["result"] == "approved"
    # idempotent second call
    body = (await client.post(f"/api/v1/payments/{card['id']}/approve")).json()
    assert body["result"] == "already"
    async with Session() as s:
        assert await WalletService(s).balance(uid) == 1000  # credited exactly once


async def test_backups_and_broadcasts(harness):
    Session, client = harness
    async with Session() as s:
        s.add(User(telegram_id=77))
        await s.commit()

    job = (await client.post("/api/v1/backups")).json()
    assert job["status"] == "pending"
    assert (await client.get("/api/v1/backups")).json()["total"] == 1
    assert (await client.delete(f"/api/v1/backups/{job['id']}")).status_code == 200

    bc = (await client.post("/api/v1/broadcasts", json={"text": "hello"})).json()
    assert bc["total"] == 1  # snapshot captured the one user
    assert (await client.get("/api/v1/broadcasts")).json()["total"] == 1


async def test_legacy_api_key_endpoints_still_work(harness):
    Session, client = harness
    # the old X-API-Key read endpoint is untouched (conftest sets test_api_key)
    fresh = AsyncClient(transport=client._transport, base_url="http://test")
    resp = await fresh.get("/api/media", headers={"X-API-Key": "test_api_key"})
    assert resp.status_code == 200
    resp = await fresh.get("/api/media", headers={"X-API-Key": "wrong"})
    assert resp.status_code == 401
    await fresh.aclose()
