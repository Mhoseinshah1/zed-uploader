"""Command-menu tests: scope resolution (defaults vs rows, search gating),
Telegram name validation, mocked set/delete_my_commands pushes, the panel
editor (save -> stored + re-pushed), and admin add/remove menu hooks."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest_asyncio
from httpx import ASGITransport
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.bot.commands_menu import (
    clear_admin_commands,
    push_admin_commands,
    push_default_commands,
    sync_all,
)
from app.core.config import settings
from app.core.redis_client import get_redis
from app.db.session import get_session
from app.models import Admin, Base, BotCommandEntry, PanelUser
from app.panel import security
from app.panel.security import hash_password
from app.panel.session import COOKIE_NAME, SessionStore
from app.services.bot_command_service import (
    DEFAULT_COMMANDS,
    BotCommandService,
    bust_cache,
    resolved_commands,
    valid_command,
)
from app.services.bot_setting_service import (
    KEY_PUBLIC_SEARCH_ENABLED,
    BotSettingService,
)

PANEL = settings.panel_path


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


def _chat_ids(mock_calls) -> set[int]:
    return {
        c.kwargs["scope"].chat_id
        for c in mock_calls
        if hasattr(c.kwargs.get("scope"), "chat_id")
    }


# --- resolution ------------------------------------------------------------
async def test_default_resolution_user_vs_admin(sqlite_maker):
    async with sqlite_maker() as s:
        # public search is off by default -> the user list drops /search
        user_cmds = await resolved_commands(s, "user")
        assert ("search", "جستجوی فایل‌ها") not in user_cmds
        assert [c for c, _ in user_cmds][:2] == ["start", "help"]

        # the admin list is the full default set (admins may always search)
        assert await resolved_commands(s, "admin") == DEFAULT_COMMANDS["admin"]

        # enabling public search re-adds /search to the user list
        await BotSettingService(s).set(KEY_PUBLIC_SEARCH_ENABLED, True)
        await bust_cache("user")
        assert ("search", "جستجوی فایل‌ها") in await resolved_commands(s, "user")


async def test_rows_override_defaults_order_and_active(sqlite_maker):
    async with sqlite_maker() as s:
        svc = BotCommandService(s)
        assert await svc.upsert("user", "start", "شروع", sort_order=1) is not None
        assert await svc.upsert("user", "ping", "پینگ", sort_order=0) is not None
        assert (
            await svc.upsert("user", "hidden", "مخفی", sort_order=2, is_active=False)
            is not None
        )
        resolved = await resolved_commands(s, "user")
        assert resolved == [("ping", "پینگ"), ("start", "شروع")]  # sorted, no inactive

        # rows fully replace the defaults (no merge)
        assert all(c != "wallet" for c, _ in resolved)

        # deleting every row -> back to defaults
        await svc.reset("user")
        assert [c for c, _ in await resolved_commands(s, "user")][:2] == ["start", "help"]


async def test_command_name_validation(sqlite_maker):
    assert valid_command("start") and valid_command("my_cmd2")
    for bad in ("Start", "has space", "", "x" * 33, "فارسی", "no-dash"):
        assert not valid_command(bad)

    async with sqlite_maker() as s:
        svc = BotCommandService(s)
        for bad in ("Start", "has space", "", "x" * 33, "فارسی"):
            assert await svc.upsert("user", bad, "توضیح") is None
        assert await svc.upsert("user", "ok", "") is None  # empty description
        assert await svc.upsert("nope", "ok", "توضیح") is None  # unknown scope
        entry = await svc.upsert("user", "/slashed", "توضیح")  # leading / stripped
        assert entry is not None and entry.command == "slashed"


async def test_sort_order_and_scope_size_limits(sqlite_maker):
    async with sqlite_maker() as s:
        svc = BotCommandService(s)
        # int4 range enforced up front instead of a DBAPIError 500 on Postgres
        assert await svc.upsert("user", "big", "توضیح", sort_order=2**31) is None
        entry = await svc.upsert("user", "big", "توضیح", sort_order=7)
        assert entry is not None
        assert await svc.update(entry.id, "توضیح", 2**31, True) is None
        assert await svc.update(entry.id, "توضیح", 8, True) is not None

        # Telegram caps setMyCommands at 100 per scope: the 101st add refuses,
        # editing an existing command still works
        s.add_all(
            BotCommandEntry(scope="admin", command=f"c{i}", description="d")
            for i in range(100)
        )
        await s.commit()
        assert await svc.upsert("admin", "one_too_many", "توضیح") is None
        assert await svc.upsert("admin", "c42", "ویرایش") is not None


# --- Telegram pushes (mocked bot) -------------------------------------------
async def test_push_and_clear_scopes(sqlite_maker):
    bot = AsyncMock()
    async with sqlite_maker() as s:
        assert await push_default_commands(bot, s) is True
        # default scope + all private chats
        assert bot.set_my_commands.await_count == 2
        scopes = [c.kwargs["scope"].type for c in bot.set_my_commands.await_args_list]
        assert scopes == ["default", "all_private_chats"]
        sent = bot.set_my_commands.await_args_list[0].args[0]
        assert [b.command for b in sent] == [c for c, _ in await resolved_commands(s, "user")]

        bot.reset_mock()
        assert await push_admin_commands(bot, s, 42) is True
        scope = bot.set_my_commands.await_args_list[0].kwargs["scope"]
        assert scope.type == "chat" and scope.chat_id == 42

    assert await clear_admin_commands(bot, 42) is True
    scope = bot.delete_my_commands.await_args_list[0].kwargs["scope"]
    assert scope.type == "chat" and scope.chat_id == 42

    # a Telegram hiccup degrades to False, never raises
    dead = AsyncMock()
    dead.set_my_commands.side_effect = RuntimeError("boom")
    dead.delete_my_commands.side_effect = RuntimeError("boom")
    async with sqlite_maker() as s:
        assert await push_default_commands(dead, s) is False
        assert await push_admin_commands(dead, s, 1) is False
    assert await clear_admin_commands(dead, 1) is False


async def test_sync_all_covers_env_and_db_admins(sqlite_maker):
    bot = AsyncMock()
    async with sqlite_maker() as s:
        s.add(Admin(telegram_id=555, role="admin", is_active=True))
        s.add(Admin(telegram_id=666, role="admin", is_active=False))  # skipped
        await s.commit()
        await sync_all(bot, s)
    # env owners 111 + 222 (conftest ADMIN_IDS) + active DB admin 555
    assert _chat_ids(bot.set_my_commands.await_args_list) == {111, 222, 555}
    assert bot.set_my_commands.await_count == 2 + 3  # default pair + one per admin


async def test_admin_start_pushes_chat_scoped_menu(sqlite_maker):
    """An admin's /start refreshes their chat-scoped list (lazy re-push)."""
    from app.bot.handlers.start import _send_welcome

    bot = AsyncMock()
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=111),  # env owner from conftest ADMIN_IDS
        chat=SimpleNamespace(id=111),
        answer=AsyncMock(),
        bot=bot,
    )
    async with sqlite_maker() as s:
        await _send_welcome(message, s)
    assert _chat_ids(bot.set_my_commands.await_args_list) == {111}


# --- panel: edit -> stored + re-pushed; admin add/remove hooks ---------------
async def _panel_client():
    engine = create_async_engine(
        "sqlite+aiosqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    from app.api.main import app

    async def _override():
        async with maker() as session:
            yield session

    app.dependency_overrides[get_session] = _override
    app.state.bot = AsyncMock()

    async with maker() as s:
        panel_user = PanelUser(username="cmds", password_hash=hash_password("pw"), tenant_id=1)
        s.add(panel_user)
        await s.commit()
        uid = panel_user.id
    csrf = security.generate_csrf()
    sid = await SessionStore(get_redis()).create({"uid": uid, "csrf": csrf})
    client = httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    client.cookies.set(COOKIE_NAME, security.sign(sid))
    return app, engine, maker, client, csrf


async def test_panel_edit_updates_list_and_repushes():
    app, engine, maker, client, csrf = await _panel_client()
    try:
        # seed the admin defaults into editable rows
        resp = await client.post(
            f"{PANEL}/commands/admin/seed", data={"csrf_token": csrf},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        async with maker() as s:
            row = await s.scalar(
                select(BotCommandEntry).where(
                    BotCommandEntry.scope == "admin",
                    BotCommandEntry.command == "panel",
                )
            )
            assert row is not None
        page = await client.get(f"{PANEL}/commands")
        assert page.status_code == 200 and "پنل مدیریت" in page.text

        # edit the description -> stored AND re-pushed chat-scoped to admins
        app.state.bot.reset_mock()
        resp = await client.post(
            f"{PANEL}/commands/{row.id}/save",
            data={
                "description": "پنل مدیریت ویرایش‌شده",
                "sort_order": "0",
                "is_active": "on",
                "csrf_token": csrf,
            },
            follow_redirects=False,
        )
        assert resp.status_code == 302 and "error" not in resp.headers["location"]
        async with maker() as s:
            saved = await s.get(BotCommandEntry, row.id)
            assert saved.description == "پنل مدیریت ویرایش‌شده"
        pushes = app.state.bot.set_my_commands.await_args_list
        assert _chat_ids(pushes) == {111, 222}  # env admins, eagerly re-pushed
        assert any(
            b.description == "پنل مدیریت ویرایش‌شده"
            for c in pushes for b in c.args[0]
        )

        # invalid command name is rejected: error redirect, nothing stored
        resp = await client.post(
            f"{PANEL}/commands/user/add",
            data={"command": "Bad Name", "description": "x", "csrf_token": csrf},
            follow_redirects=False,
        )
        assert "error=invalid" in resp.headers["location"]
        async with maker() as s:
            assert await s.scalar(
                select(BotCommandEntry).where(BotCommandEntry.scope == "user")
            ) is None
    finally:
        await client.aclose()
        app.dependency_overrides.clear()
        await engine.dispose()


async def test_settings_search_toggle_repushes_user_menu():
    """Enabling public search re-pushes the default menu including /search."""
    app, engine, maker, client, csrf = await _panel_client()
    try:
        resp = await client.post(
            f"{PANEL}/settings/search",
            data={"public_search_enabled": "on", "csrf_token": csrf},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        pushes = app.state.bot.set_my_commands.await_args_list
        assert [c.kwargs["scope"].type for c in pushes] == [
            "default", "all_private_chats",
        ]
        assert any(b.command == "search" for b in pushes[0].args[0])

        # turning it off pushes a list WITHOUT /search
        app.state.bot.reset_mock()
        await client.post(
            f"{PANEL}/settings/search",
            data={"csrf_token": csrf},
            follow_redirects=False,
        )
        pushes = app.state.bot.set_my_commands.await_args_list
        assert pushes and all(
            b.command != "search" for c in pushes for b in c.args[0]
        )
    finally:
        await client.aclose()
        app.dependency_overrides.clear()
        await engine.dispose()


async def test_panel_admin_add_and_remove_sync_menu():
    app, engine, maker, client, csrf = await _panel_client()
    try:
        resp = await client.post(
            f"{PANEL}/admins/add",
            data={"telegram_id": "888", "csrf_token": csrf},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert 888 in _chat_ids(app.state.bot.set_my_commands.await_args_list)

        async with maker() as s:
            admin = await s.scalar(select(Admin).where(Admin.telegram_id == 888))
        resp = await client.post(
            f"{PANEL}/admins/{admin.id}/remove",
            data={"csrf_token": csrf},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        cleared = app.state.bot.delete_my_commands.await_args_list
        assert _chat_ids(cleared) == {888}
        async with maker() as s:
            assert await s.scalar(select(Admin).where(Admin.telegram_id == 888)) is None
    finally:
        await client.aclose()
        app.dependency_overrides.clear()
        await engine.dispose()
