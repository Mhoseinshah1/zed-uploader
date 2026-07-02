"""LicenseService (E1) — activation, periodic re-check, graceful degradation.

Design constraints (conservative + reversible by construction):
  - DEV BYPASS: ``LICENSE_DISABLED=true`` (the default) or missing
    key/server config bypasses everything — dev, tests, and existing
    deployments never see licensing.
  - Degradation only gates NEW paid actions (online payments, plan sales,
    broadcasts). File delivery, panel viewing and all data are untouched, and
    everything re-enables the moment the license is valid again.
  - Offline grace: when the activation server is unreachable, the last-known
    -good state is honored for ``LICENSE_GRACE_DAYS`` after the last
    successful contact, then paid actions degrade (never a hard failure).

FINGERPRINT: sha256(machine_id + ":" + install_path) hex — machine_id is the
stripped contents of /etc/machine-id ("" if unreadable), install_path is the
absolute project root (parent of the ``app`` package). Both stable across
restarts.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.models.license import LicenseInfo
from app.services.providers.base import post_json as post_json  # noqa: PLC0414

log = get_logger("license")

MACHINE_ID_PATH = "/etc/machine-id"
_INSTALL_PATH = str(Path(__file__).resolve().parent.parent.parent)

# activate/check outcomes
OK = "ok"
REJECTED = "rejected"
OFFLINE = "offline"


def licensing_bypassed() -> bool:
    """True when licensing must not interfere (dev/tests/unconfigured)."""
    return (
        settings.license_disabled
        or not settings.license_key
        or not settings.license_server_url
    )


def server_fingerprint() -> str:
    """sha256(machine_id + ":" + install_path) — see the module docstring."""
    try:
        machine_id = Path(MACHINE_ID_PATH).read_text().strip()
    except OSError:
        machine_id = ""
    return hashlib.sha256(f"{machine_id}:{_INSTALL_PATH}".encode()).hexdigest()


def _parse_expiry(raw) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _aware(dt: datetime | None) -> datetime | None:
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def evaluate(row: LicenseInfo | None, now: datetime | None = None) -> bool:
    """Pure paid-features decision for a license row (bypass handled upstream)."""
    now = now or datetime.now(timezone.utc)
    if row is None or row.status != "active":
        return False
    expires = _aware(row.expires_at)
    if expires is not None and expires <= now:
        return False
    last_ok = _aware(row.last_ok_at)
    if last_ok is not None:
        grace = timedelta(days=max(0, settings.license_grace_days))
        if now - last_ok > grace:
            return False  # offline too long -> degrade until the server confirms
    return True


async def paid_features_allowed(session: AsyncSession) -> bool:
    """Gate for NEW paid actions. Never gates delivery/viewing/data."""
    if licensing_bypassed():
        return True
    row = await LicenseService(session).get_row()
    return evaluate(row)


class LicenseService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_row(self) -> LicenseInfo | None:
        return await self.session.scalar(select(LicenseInfo).limit(1))

    async def _ensure_row(self) -> LicenseInfo:
        row = await self.get_row()
        if row is None:
            row = LicenseInfo(fingerprint=server_fingerprint())
            self.session.add(row)
            await self.session.commit()
        return row

    def _mirror_to_disk(self, row: LicenseInfo) -> None:
        """Best-effort license.json mirror (DB stays the source of truth)."""
        try:
            Path(settings.license_file).write_text(
                json.dumps(
                    {
                        "license_key": row.license_key,
                        "status": row.status,
                        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
                        "allowed_install_count": row.allowed_install_count,
                        "fingerprint": row.fingerprint,
                        "last_ok_at": row.last_ok_at.isoformat() if row.last_ok_at else None,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        except OSError as exc:
            log.warning("license_mirror_failed", error=str(exc))

    async def _apply_server_state(self, row: LicenseInfo, data: dict) -> None:
        row.status = str(data.get("status", "active"))
        row.expires_at = _parse_expiry(data.get("expires_at"))
        if data.get("allowed_install_count") is not None:
            row.allowed_install_count = int(data["allowed_install_count"])
        row.last_ok_at = datetime.now(timezone.utc)
        row.last_check_at = row.last_ok_at
        await self.session.commit()
        self._mirror_to_disk(row)

    async def activate(self, key: str) -> str:
        """Activate against LICENSE_SERVER_URL. Returns ok|rejected|offline."""
        row = await self._ensure_row()
        row.license_key = key.strip()
        row.fingerprint = server_fingerprint()
        await self.session.commit()

        resp = await post_json(
            f"{settings.license_server_url.rstrip('/')}/activate",
            {"key": row.license_key, "fingerprint": row.fingerprint},
        )
        if not resp:
            row.last_check_at = datetime.now(timezone.utc)
            await self.session.commit()
            log.warning("license_activate_offline")
            return OFFLINE  # grace applies from the previous last_ok_at
        if resp.get("ok"):
            await self._apply_server_state(row, resp)
            log.info("license_activated", status=row.status)
            return OK
        row.status = str(resp.get("status", "revoked"))
        row.last_check_at = datetime.now(timezone.utc)
        await self.session.commit()
        self._mirror_to_disk(row)
        log.error("license_activation_rejected", status=row.status)
        return REJECTED

    async def check(self) -> str:
        """Periodic re-check. Returns ok|rejected|offline; never raises."""
        row = await self._ensure_row()
        key = row.license_key or settings.license_key
        if not key:
            return REJECTED
        resp = await post_json(
            f"{settings.license_server_url.rstrip('/')}/check",
            {"key": key, "fingerprint": row.fingerprint or server_fingerprint()},
        )
        if not resp:
            row.last_check_at = datetime.now(timezone.utc)
            await self.session.commit()
            log.warning("license_check_offline")
            return OFFLINE  # keep last-known-good; grace window applies
        if resp.get("ok"):
            row.license_key = key
            await self._apply_server_state(row, resp)
            return OK
        row.status = str(resp.get("status", "revoked"))
        row.last_check_at = datetime.now(timezone.utc)
        await self.session.commit()
        self._mirror_to_disk(row)
        log.error("license_check_rejected", status=row.status)
        return REJECTED


CHECK_INTERVAL = timedelta(days=1)


async def maybe_daily_check(session_maker) -> None:
    """Worker hook: re-check at most once per day; a no-op under the bypass."""
    if licensing_bypassed():
        return
    async with session_maker() as session:
        service = LicenseService(session)
        row = await service.get_row()
        last = _aware(row.last_check_at) if row else None
        if last is not None and datetime.now(timezone.utc) - last < CHECK_INTERVAL:
            return
        await service.check()
