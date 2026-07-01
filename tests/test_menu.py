"""Tests for the admin panel: reply keyboard + owner-scoped media methods.

Uses in-memory SQLite (aiosqlite) — no live DB/Redis required.
"""
from __future__ import annotations

import asyncio

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.bot import messages
from app.bot.keyboards.reply import build_admin_menu
from app.models import Base, User
from app.services.media_service import MediaService


def test_build_admin_menu_has_four_buttons():
    keyboard = build_admin_menu()
    texts = [button.text for row in keyboard.keyboard for button in row]
    assert texts == [
        messages.BTN_UPLOAD,
        messages.BTN_MY_FILES,
        messages.BTN_STATS,
        messages.BTN_SETTINGS,
    ]
    assert keyboard.resize_keyboard is True


async def _owner_scoping() -> None:
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as session:
        alice = User(telegram_id=1001, first_name="Alice")
        bob = User(telegram_id=1002, first_name="Bob")
        session.add_all([alice, bob])
        await session.commit()

        service = MediaService(session)
        media = await service.create_media(
            files=[{"telegram_file_id": "F1", "file_type": "document"}],
            owner_user_id=alice.id,
        )
        await service.log_download(media.id, telegram_id=1001, user_id=alice.id)

        # get_owned is scoped: Bob cannot fetch Alice's media.
        assert await service.get_owned(media.id, alice.id) is not None
        assert await service.get_owned(media.id, bob.id) is None

        # mutators are no-ops (return False) for the wrong owner.
        assert await service.set_active(media.id, bob.id, False) is False
        assert await service.set_active(media.id, alice.id, False) is True

        # listing / counting are scoped.
        assert len(await service.list_by_owner(alice.id)) == 1
        assert len(await service.list_by_owner(bob.id)) == 0
        assert await service.count_by_owner(alice.id) == 1
        assert await service.count_by_owner(bob.id) == 0

        # owner_stats is scoped: (media_count, total_downloads).
        assert await service.owner_stats(alice.id) == (1, 1)
        assert await service.owner_stats(bob.id) == (0, 0)

        # delete is scoped too.
        assert await service.delete_media(media.id, bob.id) is False
        assert await service.delete_media(media.id, alice.id) is True
        assert await service.count_by_owner(alice.id) == 0

    await engine.dispose()


def test_owner_scoping():
    asyncio.run(_owner_scoping())
