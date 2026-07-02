"""E1 unit tests — dev bypass, activation, degradation, grace, fingerprint.

SQLite + mocked license-server HTTP. The test env has no license config, so
the bypass is the default — exactly as required.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.services.license_service as license_service
from app.core.config import settings
from app.models import Base, Media, MediaFile
from app.services.license_service import (
    OFFLINE,
    OK,
    REJECTED,
    LicenseService,
    evaluate,
    licensing_bypassed,
    paid_features_allowed,
    server_fingerprint,
)


@pytest_asyncio.fixture
async def sqlite_maker():
    engine = create_async_engine(
        "sqlite+aiosqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _enable_licensing(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "license_disabled", False)
    monkeypatch.setattr(settings, "license_key", "KEY-1")
    monkeypatch.setattr(settings, "license_server_url", "https://lic.example")
    monkeypatch.setattr(settings, "license_file", str(tmp_path / "license.json"))


def _server(response: dict):
    async def fake(url, payload, timeout=20.0):
        return response

    return fake


# --- dev bypass (the default) ------------------------------------------------
async def test_bypass_is_default_and_allows_everything(sqlite_maker):
    assert licensing_bypassed() is True  # test env: no license config
    async with sqlite_maker() as s:
        assert await paid_features_allowed(s) is True


async def test_license_disabled_flag_bypasses_even_when_configured(
    sqlite_maker, monkeypatch, tmp_path
):
    _enable_licensing(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "license_disabled", True)
    assert licensing_bypassed() is True
    async with sqlite_maker() as s:
        assert await paid_features_allowed(s) is True


# --- activation ---------------------------------------------------------------
async def test_active_key_passes(sqlite_maker, monkeypatch, tmp_path):
    _enable_licensing(monkeypatch, tmp_path)
    monkeypatch.setattr(
        license_service, "post_json",
        _server({"ok": True, "status": "active", "expires_at": "2099-01-01",
                 "allowed_install_count": 2}),
    )
    async with sqlite_maker() as s:
        assert await LicenseService(s).activate("KEY-1") == OK
        assert await paid_features_allowed(s) is True
        row = await LicenseService(s).get_row()
        assert row.status == "active" and row.allowed_install_count == 2
        assert row.last_ok_at is not None
        # mirrored to disk (no secrets beyond what the owner configured)
        assert (tmp_path / "license.json").exists()


async def test_rejected_activation_degrades(sqlite_maker, monkeypatch, tmp_path):
    _enable_licensing(monkeypatch, tmp_path)
    monkeypatch.setattr(
        license_service, "post_json", _server({"ok": False, "status": "revoked"})
    )
    async with sqlite_maker() as s:
        assert await LicenseService(s).activate("KEY-1") == REJECTED
        assert await paid_features_allowed(s) is False


# --- graceful degradation keeps delivery + data ---------------------------------
async def test_revoked_license_never_touches_delivery_or_data(
    sqlite_maker, monkeypatch, tmp_path
):
    from app.bot import delivery as delivery_module
    from app.bot.delivery import DeliveryStatus, deliver_by_code

    _enable_licensing(monkeypatch, tmp_path)
    monkeypatch.setattr(
        license_service, "post_json", _server({"ok": False, "status": "revoked"})
    )
    monkeypatch.setattr(
        delivery_module, "send_media_file", AsyncMock(return_value=1)
    )
    async with sqlite_maker() as s:
        media = Media(code="licmedia", status="approved")
        media.files.append(
            MediaFile(sort_order=0, telegram_file_id="f", file_type="document")
        )
        s.add(media)
        await s.commit()

        await LicenseService(s).check()  # -> revoked
        assert await paid_features_allowed(s) is False  # paid actions blocked

        # file delivery to users KEEPS WORKING
        result = await deliver_by_code(AsyncMock(), s, 5, None, "licmedia")
        assert result.status is DeliveryStatus.DELIVERED
        # data intact
        assert (await s.get(Media, media.id)) is not None

        # reversible: the server says active again -> everything re-enables
        monkeypatch.setattr(
            license_service, "post_json", _server({"ok": True, "status": "active"})
        )
        assert await LicenseService(s).check() == OK
        assert await paid_features_allowed(s) is True


# --- offline grace ---------------------------------------------------------------
async def test_offline_grace_honored_then_degrades(sqlite_maker, monkeypatch, tmp_path):
    _enable_licensing(monkeypatch, tmp_path)
    monkeypatch.setattr(
        license_service, "post_json",
        _server({"ok": True, "status": "active"}),
    )
    async with sqlite_maker() as s:
        await LicenseService(s).activate("KEY-1")

        # the server goes dark
        monkeypatch.setattr(license_service, "post_json", _server({}))
        assert await LicenseService(s).check() == OFFLINE

        row = await LicenseService(s).get_row()
        # within the grace window: still allowed
        row.last_ok_at = datetime.now(timezone.utc) - timedelta(days=3)
        await s.commit()
        assert await paid_features_allowed(s) is True

        # grace lapsed: degrade (no hard failure, just gated paid actions)
        row.last_ok_at = datetime.now(timezone.utc) - timedelta(days=10)
        await s.commit()
        assert await paid_features_allowed(s) is False


async def test_locally_expired_license_degrades(sqlite_maker, monkeypatch, tmp_path):
    _enable_licensing(monkeypatch, tmp_path)
    async with sqlite_maker() as s:
        monkeypatch.setattr(
            license_service, "post_json",
            _server({"ok": True, "status": "active", "expires_at": "2000-01-01"}),
        )
        await LicenseService(s).activate("KEY-1")
        assert await paid_features_allowed(s) is False  # expiry respected locally


# --- fingerprint ------------------------------------------------------------------
def test_fingerprint_stable_and_matches_documented_hash(monkeypatch, tmp_path):
    import hashlib

    machine_file = tmp_path / "machine-id"
    machine_file.write_text("abc-machine-id\n")
    monkeypatch.setattr(license_service, "MACHINE_ID_PATH", str(machine_file))

    fp1 = server_fingerprint()
    fp2 = server_fingerprint()
    assert fp1 == fp2  # stable across calls/restarts

    expected = hashlib.sha256(
        f"abc-machine-id:{license_service._INSTALL_PATH}".encode()
    ).hexdigest()
    assert fp1 == expected  # exactly the documented sha256(machine_id:install_path)

    # unreadable machine-id -> still deterministic ("" component)
    monkeypatch.setattr(license_service, "MACHINE_ID_PATH", str(tmp_path / "missing"))
    assert server_fingerprint() == hashlib.sha256(
        f":{license_service._INSTALL_PATH}".encode()
    ).hexdigest()


# --- evaluate() is pure & conservative ---------------------------------------------
def test_evaluate_edge_cases():
    assert evaluate(None) is False  # licensing on but never activated
    from app.models import LicenseInfo

    now = datetime.now(timezone.utc)
    active = LicenseInfo(status="active", last_ok_at=now)
    assert evaluate(active) is True
    assert evaluate(LicenseInfo(status="revoked", last_ok_at=now)) is False
    assert evaluate(LicenseInfo(status="expired", last_ok_at=now)) is False
    assert evaluate(LicenseInfo(status="inactive")) is False
