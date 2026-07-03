"""I5 — media panel actions + Feature Flags UI.

Every media edit persists + audits + respects tenant/role; media search matches
code/title/caption/file_name; a feature-flag change from the panel actually
changes FeatureService gating.
"""
from __future__ import annotations

import httpx
import pytest_asyncio
from httpx import ASGITransport
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.redis_client import get_redis
from app.core.tenant_context import all_tenants, tenant_scope
from app.db.session import get_session
from app.models import (
    Base,
    Media,
    MediaFile,
    PanelAudit,
    PanelUser,
    Plan,
    Tenant,
    User,
)
from app.models.settings import FeatureFlag
from app.panel import security
from app.panel.security import hash_password
from app.panel.session import COOKIE_NAME, SessionStore
from app.services.feature_service import FeatureService

T = 2


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
            s.add(Tenant(id=T, bot_username="acmebot", bot_id=2002, status="active"))
            await s.commit()
            for r in ("owner", "content", "support"):
                pu = PanelUser(username=f"u_{r}", password_hash=hash_password("pw"),
                               tenant_id=T, role=r, is_superadmin=False)
                s.add(pu)
                await s.flush()
                ids[r] = pu.id
            await s.commit()
    with tenant_scope(T):
        async with Session() as s:
            owner_user = User(telegram_id=9001)
            s.add(owner_user)
            await s.flush()
            m = Media(code="AB12", title="سریال", status="approved", download_count=7,
                      owner_user_id=owner_user.id)
            s.add(m)
            await s.flush()
            s.add(MediaFile(media_id=m.id, file_type="document", file_name="report.pdf", telegram_file_id="x"))
            await s.commit()
            ids["media"] = m.id
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


async def _client(app, uid):
    csrf = security.generate_csrf()
    sid = await SessionStore(get_redis()).create({"uid": uid, "csrf": csrf})
    client = httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t")
    client.cookies.set(COOKIE_NAME, security.sign(sid))
    return client, csrf


async def test_media_edit_persists_and_audits(env):
    app, Session, ids = env
    client, csrf = await _client(app, ids["content"])  # content role may edit
    try:
        r = await client.post(
            f"/panel/media/{ids['media']}/edit",
            data={"title": "عنوان نو", "caption": "کپشن", "download_limit": "10",
                  "auto_delete_seconds": "60", "status": "pending", "csrf_token": csrf},
            follow_redirects=False,
        )
        assert r.status_code == 302
        await client.post(f"/panel/media/{ids['media']}/protect", data={"csrf_token": csrf}, follow_redirects=False)
        await client.post(f"/panel/media/{ids['media']}/reset_count", data={"csrf_token": csrf}, follow_redirects=False)
    finally:
        await client.aclose()
    with tenant_scope(T):
        async with Session() as s:
            m = await s.get(Media, ids["media"])
            actions = {a.action for a in (await s.scalars(select(PanelAudit))).all()}
    assert m.title == "عنوان نو" and m.caption == "کپشن"
    assert m.download_limit == 10 and m.auto_delete_seconds == 60 and m.status == "pending"
    assert m.protect_content is True and m.download_count == 0
    assert {"media_edit", "media_protect", "media_reset_count"} <= actions


async def test_media_edit_denied_for_support(env):
    app, Session, ids = env
    client, csrf = await _client(app, ids["support"])  # support can't touch media
    try:
        r = await client.post(
            f"/panel/media/{ids['media']}/edit",
            data={"title": "x", "csrf_token": csrf}, follow_redirects=False,
        )
        assert r.status_code == 403
    finally:
        await client.aclose()


async def test_media_search_matches_title_and_filename(env):
    app, Session, ids = env
    client, _ = await _client(app, ids["owner"])
    try:
        by_title = await client.get("/panel/media", params={"q": "سریال"})
        by_file = await client.get("/panel/media", params={"q": "report"})
        by_code = await client.get("/panel/media", params={"q": "AB12"})
        for resp in (by_title, by_file, by_code):
            assert resp.status_code == 200 and "AB12" in resp.text
        # a non-matching query returns no rows
        none = await client.get("/panel/media", params={"q": "zzz-nomatch"})
        assert "AB12" not in none.text
    finally:
        await client.aclose()


async def test_media_detail_shows_deep_link_and_owner(env):
    app, Session, ids = env
    client, _ = await _client(app, ids["owner"])
    try:
        resp = await client.get(f"/panel/media/{ids['media']}")
        assert resp.status_code == 200
        assert "acmebot" in resp.text  # deep link uses the tenant bot username
        assert f"/panel/users/" in resp.text  # owner link
    finally:
        await client.aclose()


async def test_feature_flag_change_affects_gating(env):
    app, Session, ids = env
    # a user on plan "pro"
    with tenant_scope(T):
        async with Session() as s:
            u = User(telegram_id=7777, plan="pro")
            s.add(u)
            await s.commit()
            uid = u.id

    # initially no flag -> feature is OFF
    with tenant_scope(T):
        async with Session() as s:
            u = await s.get(User, uid)
            assert await FeatureService.is_enabled(s, "protect_content", u) is False

    # owner enables the flag from the panel with min plan free
    client, csrf = await _client(app, ids["owner"])
    try:
        r = await client.post(
            "/panel/features/protect_content",
            data={"is_enabled": "on", "plan": "", "csrf_token": csrf},
            follow_redirects=False,
        )
        assert r.status_code == 302
    finally:
        await client.aclose()

    # now the SAME gating call returns True -> the panel change took effect
    with tenant_scope(T):
        async with Session() as s:
            u = await s.get(User, uid)
            assert await FeatureService.is_enabled(s, "protect_content", u) is True
            flag = await s.scalar(select(FeatureFlag).where(FeatureFlag.key == "protect_content"))
            audit = await s.scalar(select(PanelAudit).where(PanelAudit.action == "feature_flag"))
    assert flag.is_enabled is True and audit is not None


async def test_features_page_is_owner_only(env):
    app, Session, ids = env
    client, _ = await _client(app, ids["content"])
    try:
        assert (await client.get("/panel/features")).status_code == 403
    finally:
        await client.aclose()
