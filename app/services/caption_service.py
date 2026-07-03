"""Caption tools (J3) — per-tenant link stripping + signature.

Applied at DELIVERY time (the single choke point every path — single, batch,
album — flows through), so toggling the settings affects already-uploaded
files immediately and no stored caption is destructively rewritten.
"""
from __future__ import annotations

import re

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.bot_setting_service import (
    KEY_CAPTION_SIGNATURE,
    KEY_CAPTION_STRIP_LINKS,
    BotSettingService,
)

# URLs (http/https, www., t.me/, telegram.me/) and @mentions
_LINK_RE = re.compile(
    r"(?:https?://\S+|www\.\S+|(?:t|telegram)\.me/\S+)", re.IGNORECASE
)
_MENTION_RE = re.compile(r"@\w+")
_MULTISPACE_RE = re.compile(r"[ \t]{2,}")
_MULTINEWLINE_RE = re.compile(r"\n{3,}")


def strip_links(text: str) -> str:
    """Remove URLs and @mentions; collapse the leftover whitespace."""
    out = _LINK_RE.sub("", text)
    out = _MENTION_RE.sub("", out)
    out = _MULTISPACE_RE.sub(" ", out)
    out = _MULTINEWLINE_RE.sub("\n\n", out)
    return out.strip()


async def apply_caption_tools(
    session: AsyncSession, caption: str | None
) -> str | None:
    """The delivery-time caption: optionally stripped, optionally signed."""
    setting = BotSettingService(session)
    text = caption or ""
    if await setting.get_bool(KEY_CAPTION_STRIP_LINKS, False):
        text = strip_links(text)
    signature = (await setting.get_raw(KEY_CAPTION_SIGNATURE) or "").strip()
    if signature:
        text = f"{text}\n\n{signature}" if text else signature
    return text or None
