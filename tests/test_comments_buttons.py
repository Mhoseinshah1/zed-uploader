"""J8 — media comments (moderated) + custom menu buttons (whitelisted actions)."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.bot import messages
from app.bot.handlers.custom_buttons import (
    MatchesCustomButton,
    _run_action,
    handle_custom_button,
)
from app.bot.keyboards.reply import build_user_menu
from app.core.tenant_context import all_tenants, tenant_scope
from app.models import Base, CustomButton, MediaComment, Tenant, User
from app.services.comment_service import CommentService
from app.services.custom_button_service import ACTION_WHITELIST, CustomButtonService

T_A, T_B = 2, 3


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
            s.add(Tenant(id=T_A, bot_username="a", bot_id=2002, status="active"))
            s.add(Tenant(id=T_B, bot_username="b", bot_id=3003, status="active"))
            await s.commit()
    try:
        yield Session
    finally:
        await engine.dispose()


def _msg(text: str) -> AsyncMock:
    msg = AsyncMock()
    msg.text = text
    return msg


# --- comments -----------------------------------------------------------------
async def test_comment_lifecycle_pending_approve_reject_delete(sm):
    with tenant_scope(T_A):
        async with sm() as s:
            svc = CommentService(s)
            c = await svc.create(10, 77, "  عالی بود  ")
            assert c.status == "pending" and c.body == "عالی بود"
            assert await svc.approved_for(10) == []          # moderated: hidden

            assert await svc.set_status(c.id, "approved")
            assert [r.id for r in await svc.approved_for(10)] == [c.id]

            assert await svc.set_status(c.id, "rejected")
            assert await svc.approved_for(10) == []

            assert not await svc.set_status(c.id, "bogus")   # unknown status refused
            assert await svc.delete(c.id)
            assert await s.get(MediaComment, c.id) is None


async def test_comment_body_truncated_and_listing_by_status(sm):
    with tenant_scope(T_A):
        async with sm() as s:
            svc = CommentService(s)
            c = await svc.create(11, 77, "x" * 5000)
            assert len(c.body) == 2000
            await svc.create(11, 78, "دوم")
            pending = await svc.list_by_status("pending")
            assert len(pending) == 2
            assert await svc.list_by_status("approved") == []


async def test_comments_are_tenant_scoped(sm):
    with tenant_scope(T_A):
        async with sm() as s:
            c = await CommentService(s).create(12, 77, "فقط تنانت A")
            await CommentService(s).set_status(c.id, "approved")
    with tenant_scope(T_B):
        async with sm() as s:
            assert await CommentService(s).approved_for(12) == []
            assert await CommentService(s).list_by_status("approved") == []
    with tenant_scope(T_A):
        async with sm() as s:
            assert len(await CommentService(s).approved_for(12)) == 1


# --- custom buttons: creation rules --------------------------------------------
async def test_button_create_enforces_type_whitelist_and_url_scheme(sm):
    with tenant_scope(T_A):
        async with sm() as s:
            svc = CustomButtonService(s)
            assert await svc.create("x", "exec", "rm -rf /") is None       # bad type
            assert await svc.create("x", "action", "os.system") is None    # not whitelisted
            assert await svc.create("x", "url", "javascript:alert(1)") is None
            assert await svc.create("", "message", "hi") is None           # empty label

            ok_url = await svc.create("کانال ما", "url", "https://t.me/x")
            ok_msg = await svc.create("قوانین", "message", "متن قوانین")
            ok_act = await svc.create("راهنما", "action", ACTION_WHITELIST[0])
            assert ok_url and ok_msg and ok_act
            assert len(await svc.list_all()) == 3


async def test_button_match_active_only_and_tenant_scoped(sm):
    with tenant_scope(T_A):
        async with sm() as s:
            svc = CustomButtonService(s)
            b = await svc.create("قوانین", "message", "متن")
            assert (await svc.by_label("قوانین")).id == b.id
            await svc.toggle(b.id)                            # deactivate
            assert await svc.by_label("قوانین") is None
            assert await MatchesCustomButton()(_msg("قوانین"), s) is False
            await svc.toggle(b.id)                            # reactivate
            match = await MatchesCustomButton()(_msg("قوانین"), s)
            assert match and match["custom_button"].id == b.id
    with tenant_scope(T_B):                                   # other tenant: invisible
        async with sm() as s:
            assert await CustomButtonService(s).by_label("قوانین") is None


# --- custom buttons: dispatch behaviors ----------------------------------------
async def test_button_dispatch_url_message_and_whitelisted_actions(sm):
    with tenant_scope(T_A):
        async with sm() as s:
            u = User(telegram_id=9401)
            s.add(u)
            await s.commit()

            # message type -> stored text verbatim
            msg = _msg("قوانین")
            btn = SimpleNamespace(label="قوانین", type="message", value="متن قوانین")
            await handle_custom_button(msg, s, u, btn)
            assert msg.answer.call_args.args[0] == "متن قوانین"

            # url type -> link button attached
            msg = _msg("کانال")
            btn = SimpleNamespace(label="کانال", type="url", value="https://t.me/x")
            await handle_custom_button(msg, s, u, btn)
            markup = msg.answer.call_args.kwargs["reply_markup"]
            assert markup.inline_keyboard[0][0].url == "https://t.me/x"

            # action: help + wallet answer; unknown action is a strict no-op
            for action in ACTION_WHITELIST:
                msg = _msg("x")
                await _run_action(msg, s, u, action)
                assert msg.answer.await_count == 1
            msg = _msg("x")
            await _run_action(msg, s, u, "__import__('os')")
            msg.answer.assert_not_awaited()


async def test_user_menu_renders_custom_labels(sm):
    kb = build_user_menu(custom_labels=("قوانین", "کانال ما"))
    texts = [b.text for row in kb.keyboard for b in row]
    assert "قوانین" in texts and "کانال ما" in texts
    assert texts.index("قوانین") < texts.index("کانال ما")  # sort order preserved
    # without custom labels the menu is unchanged
    base = build_user_menu()
    assert len(base.keyboard) == len(kb.keyboard) - 2
