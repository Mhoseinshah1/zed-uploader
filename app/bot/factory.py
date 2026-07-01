"""Shared bot + dispatcher factory used by both the API and the bot process.

Middleware order (Section 7): DbSessionMiddleware first (injects ``session``),
then UserContextMiddleware (upserts the user, injects ``db_user``).
"""
from __future__ import annotations

from aiogram import Bot, Dispatcher

from app.bot.handlers import common, start, upload
from app.bot.middlewares import DbSessionMiddleware, UserContextMiddleware
from app.core.config import settings
from app.db.session import async_session_maker


def create_bot() -> Bot:
    """Construct the Bot (no network I/O)."""
    return Bot(token=settings.bot_token)


_dispatcher: Dispatcher | None = None


def create_dispatcher() -> Dispatcher:
    """Return the shared Dispatcher (built once).

    Handler routers are module-level singletons and can only be attached to a
    single dispatcher, so the dispatcher itself is cached and reused. This is
    safe: aiogram binds the Bot at ``feed_update`` / ``start_polling`` time, not
    at dispatcher construction.
    """
    global _dispatcher
    if _dispatcher is not None:
        return _dispatcher

    dispatcher = Dispatcher()

    # Registered on `update` (not just `message`) so session/db_user are injected
    # for every update type — allowed_updates includes callback_query, and future
    # inline buttons will need them. Order matters: session first, then user.
    dispatcher.update.middleware(DbSessionMiddleware(async_session_maker))
    dispatcher.update.middleware(UserContextMiddleware())

    dispatcher.include_router(start.router)
    dispatcher.include_router(upload.router)
    # Catch-all router LAST so it never shadows start/upload.
    dispatcher.include_router(common.router)

    _dispatcher = dispatcher
    return dispatcher
