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
os.environ.setdefault("SESSION_SECRET", "test_session_secret")

# A per-run Fernet key so the tenant token-crypto helpers work offline (F1).
# Generated fresh each run — never a committed secret.
from cryptography.fernet import Fernet as _Fernet  # noqa: E402

os.environ.setdefault("TENANT_TOKEN_KEY", _Fernet.generate_key().decode())

# Use fakeredis for the whole suite so Redis-backed features (rate limiting,
# panel sessions, panel login lockout) behave deterministically offline.
import fakeredis.aioredis as _fakeredis  # noqa: E402

import app.core.redis_client as _redis_client  # noqa: E402

_redis_client._client = _fakeredis.FakeRedis(decode_responses=True)

import pytest
from fastapi.testclient import TestClient

# Importing app.api.main flips the global event-loop policy to uvloop as a
# side effect, which breaks pytest-asyncio's per-test loop creation for the
# first test that triggers the import. Import it once up front and pin the
# default policy for the whole suite.
import app.api.main  # noqa: E402,F401
import asyncio  # noqa: E402

asyncio.set_event_loop_policy(None)


@pytest.fixture(autouse=True)
def _fresh_fakeredis():
    """Give every test a clean, empty fake Redis.

    Redis-backed guards (e.g. the purchase double-tap lock) intentionally
    outlive a single call via a short TTL. The suite shares one process, so
    without a per-test reset a lingering key could leak between tests — which
    matters most when successive tests reuse the same synthetic ids.
    """
    _redis_client._client = _fakeredis.FakeRedis(decode_responses=True)
    yield


@pytest.fixture(autouse=True)
def _tenant_context():
    """Default every test to the platform tenant (F1).

    The DB guard fails closed without a tenant context, so the whole existing
    suite runs as tenant 1 (which the schema seeds). Isolation tests override
    this by entering their own ``tenant_scope`` / ``all_tenants``. Sync fixture
    on purpose: the ContextVar is set in the outer context so the value
    propagates into pytest-asyncio's per-test task.
    """
    from app.core.tenant_context import PLATFORM_TENANT_ID, reset_tenant, set_tenant

    token = set_tenant(PLATFORM_TENANT_ID)
    yield
    reset_tenant(token)


@pytest.fixture()
def client():
    from app.api.main import app

    with TestClient(app) as test_client:
        yield test_client
