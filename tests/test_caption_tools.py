"""J3 — caption tools: link/mention stripping + signature at delivery time."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest_asyncio
from aiogram.types import User as TgUser
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.bot.delivery as delivery_mod
from app.bot.delivery import DeliveryStatus, deliver_by_code
from app.core.tenant_context import all_tenants, tenant_scope
from app.models import Base, Media, MediaFile, Tenant, User
from app.services.bot_setting_service import (
    KEY_CAPTION_SIGNATURE,
    KEY_CAPTION_STRIP_LINKS,
    BotSettingService,
)
from app.services.caption_service import apply_caption_tools, strip_links

T = 2


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
            s.add(Tenant(id=T, bot_username="a", bot_id=2002, status="active"))
            await s.commit()
    try:
        yield Session
    finally:
        await engine.dispose()


def test_strip_links_pure():
    src = "دانلود از https://evil.com/x و t.me/spam و @spammer اینجا  www.bad.io"
    out = strip_links(src)
    for gone in ("https://", "t.me/", "@spammer", "www.bad.io"):
        assert gone not in out
    assert "دانلود از" in out and "اینجا" in out  # the prose survives


async def test_disabled_caption_unchanged(sm):
    with tenant_scope(T):
        async with sm() as s:
            out = await apply_caption_tools(s, "متن https://x.io @m")
    assert out == "متن https://x.io @m"  # both tools off -> untouched


async def test_strip_and_signature_combined(sm):
    with tenant_scope(T):
        async with sm() as s:
            st = BotSettingService(s)
            await st.set(KEY_CAPTION_STRIP_LINKS, True)
            await st.set(KEY_CAPTION_SIGNATURE, "@mychannel")
        async with sm() as s:
            out = await apply_caption_tools(s, "فیلم عالی https://spam.io")
            assert out == "فیلم عالی\n\n@mychannel"
            # empty caption -> just the signature
            assert await apply_caption_tools(s, None) == "@mychannel"


async def test_delivery_applies_tools_single_and_multifile(sm, monkeypatch):
    """The transform rides deliver_by_code — the one path single/batch/album use."""
    sent = []

    async def _fake_send(bot, chat_id, media_file, *, caption=None,
                         protect_content=False, reply_markup=None, **kw):
        sent.append(caption)
        return 1

    monkeypatch.setattr(delivery_mod, "send_media_file", _fake_send)
    with tenant_scope(T):
        async with sm() as s:
            st = BotSettingService(s)
            await st.set(KEY_CAPTION_STRIP_LINKS, True)
            await st.set(KEY_CAPTION_SIGNATURE, "@sig")
            s.add(User(telegram_id=7001))
            m = Media(code="CAP", status="approved", caption="متن @mention")
            s.add(m)
            await s.flush()
            # multi-file media (album/batch create these; same delivery path)
            s.add_all([
                MediaFile(media_id=m.id, file_type="document", telegram_file_id="f1"),
                MediaFile(media_id=m.id, file_type="document", telegram_file_id="f2"),
            ])
            await s.commit()
        async with sm() as s:
            res = await deliver_by_code(
                AsyncMock(), s, chat_id=7001,
                user=TgUser(id=7001, is_bot=False, first_name="x"), code="CAP",
            )
    assert res.status is DeliveryStatus.DELIVERED
    assert sent[0] == "متن\n\n@sig"  # first file: stripped + signed
    assert sent[1] is None           # subsequent files carry no caption
