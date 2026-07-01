"""Test configuration.

Environment variables are set BEFORE any ``app`` import so that
``app.core.config.Settings()`` (constructed at import time) picks them up.
Tests never touch a live DB/Redis: the rate limiter is fail-open when Redis is
offline, and the covered endpoints do not hit the database.
"""
from __future__ import annotations

import os

os.environ.setdefault("BOT_TOKEN", "123456:TESTTOKEN0123456789ABCDEFabcdefghij")
os.environ.setdefault("BOT_USERNAME", "zeduploader_test_bot")
os.environ.setdefault("ADMIN_IDS", "111,222")
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://uploader:uploader_password@localhost:5432/uploader_bot",
)
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("API_KEY", "test_api_key")
os.environ.setdefault("WEBHOOK_SECRET", "test_webhook_secret")
os.environ.setdefault("WEBHOOK_PATH", "/telegram/webhook")
os.environ.setdefault("DOMAIN", "https://example.com")

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client():
    from app.api.main import app

    with TestClient(app) as test_client:
        yield test_client
