"""Channel preview auto-post (J5) — best-effort, idempotent, tenant-scoped.

When a media becomes published (approved + active) and the tenant has enabled
the preview channel, post a short preview there with a «دریافت فایل» button
deep-linking back to THIS tenant's bot. Never raises into the caller — a
channel misconfiguration or Telegram error must not break upload/approve.
UNIQUE(tenant_id, media_id) on media_previews makes it exactly-once.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.media import Media
from app.models.preview import MediaPreview
from app.services.bot_setting_service import (
    KEY_PREVIEW_CHANNEL_ID,
    KEY_PREVIEW_ENABLED,
    BotSettingService,
)

log = get_logger("preview")


async def _build_tenant_bot(session: AsyncSession):
    """Throwaway Bot for the CURRENT tenant (platform uses the env token)."""
    try:
        from aiogram import Bot

        from app.core.config import settings
        from app.core.tenant_context import PLATFORM_TENANT_ID, current_tenant
        from app.models.tenant import Tenant
        from app.services.tenant_service import TenantService

        tid = current_tenant()
        if not isinstance(tid, int):
            return None
        tenant = await session.scalar(select(Tenant).where(Tenant.id == tid))
        token = TenantService.decrypt_token(tenant) if tenant else None
        if not token and tid == PLATFORM_TENANT_ID:
            token = settings.bot_token or None
        return Bot(token=token) if token else None
    except Exception:
        return None


async def maybe_post_preview(session: AsyncSession, media: Media, bot=None) -> bool:
    """Post the channel preview if configured. True only when actually posted."""
    try:
        if media is None or media.status != "approved" or not media.is_active:
            return False
        setting = BotSettingService(session)
        if not await setting.get_bool(KEY_PREVIEW_ENABLED, False):
            return False
        raw = (await setting.get_raw(KEY_PREVIEW_CHANNEL_ID) or "").strip()
        try:
            channel_id = int(raw)
        except ValueError:
            return False

        # idempotent: one preview per media (fast check + DB unique)
        existing = await session.scalar(
            select(MediaPreview.id).where(MediaPreview.media_id == media.id)
        )
        if existing is not None:
            return False

        from app.bot import messages
        from app.bot.keyboards.inline import build_url_button
        from app.services.media_service import MediaService

        link = await MediaService(session).deep_link(media)
        text = messages.preview_post(media.title or media.code)
        markup = build_url_button(messages.BTN_GET_FILE, link)

        own_bot = False
        if bot is None:
            bot = await _build_tenant_bot(session)
            own_bot = bot is not None
        if bot is None:
            return False
        try:
            if media.thumbnail_file_id:
                sent = await bot.send_photo(
                    chat_id=channel_id, photo=media.thumbnail_file_id,
                    caption=text, reply_markup=markup,
                )
            else:
                sent = await bot.send_message(
                    chat_id=channel_id, text=text, reply_markup=markup
                )
        finally:
            if own_bot:
                try:
                    await bot.session.close()
                except Exception:
                    pass

        session.add(
            MediaPreview(
                media_id=media.id, channel_id=channel_id,
                message_id=getattr(sent, "message_id", None),
            )
        )
        try:
            await session.commit()
        except IntegrityError:  # concurrent double-post folded into one
            await session.rollback()
        return True
    except Exception as exc:  # NEVER break the approve/upload flow
        log.warning("preview_post_failed", media_id=getattr(media, "id", None), error=str(exc))
        try:
            await session.rollback()
        except Exception:
            pass
        return False
