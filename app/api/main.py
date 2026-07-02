"""FastAPI application entry point.

The lifespan builds the shared bot + dispatcher and stores them on
``app.state`` so the webhook route can feed updates into aiogram. It performs
no network I/O (webhook registration / admin seeding happen in the bot
process), which keeps the app importable and testable offline.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import (
    admin_v1,
    ads,
    health,
    media,
    pay,
    stats,
    tenant_webhook,
    users,
    webhook,
)
from app.bot.factory import create_bot, create_dispatcher
from app.bot.registry import BotRegistry
from app.core.config import settings
from app.core.logging import get_logger, setup_logging
from app.db.session import async_session_maker
from app.panel.main import setup_panel

log = get_logger("api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    bot = create_bot()
    dispatcher = create_dispatcher()
    app.state.bot = bot
    app.state.dp = dispatcher
    # F2: the multi-bot registry. Handlers reach it via the dispatcher context
    # (dispatcher["registry"]); the per-tenant webhook route reads app.state.
    registry = BotRegistry(async_session_maker)
    app.state.registry = registry
    dispatcher["registry"] = registry
    try:  # record the installed version; never block startup (e.g. no DB in tests)
        from app.core.tenant_context import PLATFORM_TENANT_ID, tenant_scope
        from app.core.version import sync_version

        with tenant_scope(PLATFORM_TENANT_ID):
            async with async_session_maker() as session:
                await sync_version(session)
    except Exception:
        pass
    # Register every active customer bot + (re)set its webhook (best-effort).
    await registry.load_active()
    log.info("api_started", project=settings.project_name, bots=len(registry.bot_ids()))
    try:
        yield
    finally:
        await registry.close()
        await bot.session.close()
        log.info("api_stopped")


app = FastAPI(title=settings.project_name, version="1.0.0", lifespan=lifespan)

app.include_router(health.router)
app.include_router(media.router)
app.include_router(users.router)
app.include_router(stats.router)
app.include_router(webhook.router)
app.include_router(tenant_webhook.router)
app.include_router(pay.router)
app.include_router(ads.router)
app.include_router(admin_v1.router)

# Web admin panel (Phase 4): mounts /panel, static, security headers, auth handler.
setup_panel(app)
