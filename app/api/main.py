"""FastAPI application entry point.

The lifespan builds the shared bot + dispatcher and stores them on
``app.state`` so the webhook route can feed updates into aiogram. It performs
no network I/O (webhook registration / admin seeding happen in the bot
process), which keeps the app importable and testable offline.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import health, media, stats, users, webhook
from app.bot.factory import create_bot, create_dispatcher
from app.core.config import settings
from app.core.logging import get_logger, setup_logging
from app.panel.main import setup_panel

log = get_logger("api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    bot = create_bot()
    dispatcher = create_dispatcher()
    app.state.bot = bot
    app.state.dp = dispatcher
    log.info("api_started", project=settings.project_name)
    try:
        yield
    finally:
        await bot.session.close()
        log.info("api_stopped")


app = FastAPI(title=settings.project_name, version="1.0.0", lifespan=lifespan)

app.include_router(health.router)
app.include_router(media.router)
app.include_router(users.router)
app.include_router(stats.router)
app.include_router(webhook.router)

# Web admin panel (Phase 4): mounts /panel, static, security headers, auth handler.
setup_panel(app)
