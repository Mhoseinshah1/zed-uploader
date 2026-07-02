"""App versioning (E3): VERSION file = code version; bot_settings row =
installed version, synced forward on boot (best-effort, never blocks)."""
from __future__ import annotations

from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

VERSION_FILE = Path(__file__).resolve().parent.parent.parent / "VERSION"
VERSION_SETTING_KEY = "app_version"


def code_version() -> str:
    """The semantic version shipped with this code tree."""
    try:
        return VERSION_FILE.read_text().strip() or "0.0.0"
    except OSError:
        return "0.0.0"


def _as_tuple(version: str) -> tuple[int, ...]:
    parts = []
    for chunk in version.split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts or [0])


def is_newer(candidate: str, current: str) -> bool:
    return _as_tuple(candidate) > _as_tuple(current)


async def installed_version(session: AsyncSession) -> str:
    from app.services.bot_setting_service import BotSettingService

    return await BotSettingService(session).get_raw(VERSION_SETTING_KEY) or "0.0.0"


async def sync_version(session: AsyncSession) -> str:
    """Record the code version as installed when it moved forward."""
    from app.services.bot_setting_service import BotSettingService

    current = await installed_version(session)
    code = code_version()
    if is_newer(code, current):
        await BotSettingService(session).set(VERSION_SETTING_KEY, code)
        return code
    return current
