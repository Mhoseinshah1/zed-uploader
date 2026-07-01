"""Bot process entry point.

BOT_MODE=polling  -> long polling (the bot process handles updates directly).
BOT_MODE=webhook  -> register the webhook, then idle (the API process feeds
                     updates into the dispatcher via /telegram/webhook).

Admin rows are seeded from ADMIN_IDS on startup in both modes.
"""
from __future__ import annotations

import asyncio

from app.bot.factory import create_bot, create_dispatcher
from app.core.config import settings
from app.core.logging import get_logger, setup_logging
from app.db.session import async_session_maker
from app.services.admin_service import AdminService

log = get_logger("bot")


async def _seed_admins() -> None:
    async with async_session_maker() as session:
        await AdminService(session).ensure_seed_admins(settings.admin_id_list)


async def run_polling() -> None:
    bot = create_bot()
    dispatcher = create_dispatcher()
    await _seed_admins()
    await bot.delete_webhook(drop_pending_updates=True)
    log.info("bot_polling_start", username=settings.bot_username)
    try:
        await dispatcher.start_polling(bot)
    finally:
        await bot.session.close()


async def run_webhook() -> None:
    bot = create_bot()
    await _seed_admins()
    await bot.set_webhook(
        url=settings.webhook_url,
        secret_token=settings.webhook_secret,
        drop_pending_updates=True,
        allowed_updates=["message", "callback_query"],
    )
    log.info("webhook_set", url=settings.webhook_url)
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        await bot.session.close()


async def main() -> None:
    setup_logging()
    if settings.bot_mode.lower() == "polling":
        await run_polling()
    else:
        await run_webhook()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
