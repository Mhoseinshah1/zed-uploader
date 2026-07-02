"""Shared bot + dispatcher factory used by both the API and the bot process.

Middleware order (Section 7): DbSessionMiddleware first (injects ``session``),
then UserContextMiddleware (upserts the user, injects ``db_user``).
"""
from __future__ import annotations

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.redis import RedisStorage

from app.bot.handlers import (
    admins,
    ads,
    albums,
    batch,
    billing,
    billing_owner,
    broadcast,
    channels,
    commands,
    common,
    folders,
    menu,
    reports,
    review,
    search,
    start,
    stars,
    upload,
)
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

    # Redis-backed FSM storage so multi-step admin flows survive restarts.
    # from_url builds the client lazily (no connection at construction).
    storage = RedisStorage.from_url(settings.redis_url)
    dispatcher = Dispatcher(storage=storage)

    # Registered on `update` (not just `message`) so session/db_user are injected
    # for every update type — callback_query included. Order: session then user.
    dispatcher.update.middleware(DbSessionMiddleware(async_session_maker))
    dispatcher.update.middleware(UserContextMiddleware())

    # Order matters. `batch` must precede `upload` so its StateFilter(collecting)
    # media handler wins while batching; the catch-all `common` MUST stay last.
    dispatcher.include_router(start.router)
    # Slash-command aliases before the button routers, so a command typed
    # mid-FSM-flow preempts the state-bound text handlers (and cancels it).
    dispatcher.include_router(commands.router)
    dispatcher.include_router(menu.router)
    dispatcher.include_router(review.router)
    dispatcher.include_router(ads.router)
    dispatcher.include_router(reports.router)
    dispatcher.include_router(folders.router)
    dispatcher.include_router(channels.router)
    dispatcher.include_router(admins.router)
    dispatcher.include_router(broadcast.router)
    dispatcher.include_router(billing.router)
    dispatcher.include_router(stars.router)
    dispatcher.include_router(billing_owner.router)
    dispatcher.include_router(batch.router)
    # `albums` after batch (so batch-collecting still grabs parts) and before
    # upload (so grouped media buffer instead of each becoming its own Media).
    dispatcher.include_router(albums.router)
    dispatcher.include_router(upload.router)
    # `search` after the reply-keyboard routers so a menu-button tap while a
    # search is active still routes to its own handler (which clears state).
    dispatcher.include_router(search.router)
    dispatcher.include_router(common.router)

    _dispatcher = dispatcher
    return dispatcher
