"""Shared file-delivery coroutine used by the /start deep link and the
force-join recheck callback.

Order (Section 1): status check (no claim) -> force-join gate (no claim) ->
password gate (no claim) -> atomic claim + send + log + schedule auto-delete.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from aiogram import Bot
from aiogram.types import User as TgUser
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards.inline import build_delivered_actions, build_url_button
from app.bot.sender import notify_auto_delete, send_media_file
from app.core.config import settings
from app.core.logging import get_logger
from app.core.redis_client import get_redis
from app.models.channel import RequiredChannel
from app.models.media import Media
from app.services.ad_service import AdService
from app.services.autodelete import AutoDeleteQueue
from app.services.media_service import MediaService, MediaStatus
from app.services.membership import unjoined_channels
from app.services.user_service import UserService

log = get_logger("delivery")


async def send_placement_ads(
    bot: Bot, session: AsyncSession, chat_id: int, telegram_id: int, placement: str
) -> None:
    """Best-effort ads for a placement — any failure here must NEVER affect
    delivery, so the whole thing is wrapped and errors are only logged."""
    try:
        from app.bot import messages

        db_user = await UserService(session).get_by_telegram_id(telegram_id)
        plan = db_user.effective_plan if db_user else "free"
        service = AdService(session)
        for ad in await service.pick_for_placement(placement, plan):
            markup = None
            if ad.button_text and ad.button_url:
                click_url = f"{settings.domain.rstrip('/')}/ad/{ad.id}/click"
                markup = build_url_button(ad.button_text, click_url)
            await bot.send_message(
                chat_id, messages.ad_view(ad.title, ad.text), reply_markup=markup
            )
            await service.record_impression(ad.id)
    except Exception as exc:  # ads are never allowed to break delivery
        log.warning("ad_send_failed", placement=placement, error=str(exc))


class DeliveryStatus(str, Enum):
    NOT_FOUND = "not_found"
    INACTIVE = "inactive"
    LIMIT_REACHED = "limit_reached"
    GATED = "gated"
    PASSWORD_REQUIRED = "password_required"
    DELIVERED = "delivered"
    FAILED = "failed"
    BLOCKED = "blocked"  # I1: a blocked user is refused, even via a deep link
    PLAN_REQUIRED = "plan_required"        # J6: needs a higher plan
    PAYMENT_REQUIRED = "payment_required"  # J6: paid file, no entitlement yet
    QUOTA_EXCEEDED = "quota_exceeded"      # J6: free daily quota exhausted


_FROM_MEDIA_STATUS = {
    MediaStatus.NOT_FOUND: DeliveryStatus.NOT_FOUND,
    MediaStatus.INACTIVE: DeliveryStatus.INACTIVE,
    MediaStatus.LIMIT_REACHED: DeliveryStatus.LIMIT_REACHED,
}


@dataclass
class DeliveryResult:
    status: DeliveryStatus
    media: Media | None = None
    channels: list[RequiredChannel] = field(default_factory=list)
    sent_ids: list[int] = field(default_factory=list)


async def deliver_by_code(
    bot: Bot,
    session: AsyncSession,
    chat_id: int,
    user: TgUser | None,
    code: str,
    *,
    password_verified: bool = False,
) -> DeliveryResult:
    service = MediaService(session)
    user_id = user.id if user else chat_id

    # (a0) blocked-user guard (defense-in-depth): a blocked, non-admin user is
    # refused a file even via a deep link, independent of the bot middleware.
    if user is not None:
        db = await UserService(session).get_by_telegram_id(user.id)
        if db is not None and db.is_blocked:
            from app.services.admin_service import AdminService

            if not await AdminService.is_admin(session, user.id):
                return DeliveryResult(DeliveryStatus.BLOCKED)

    # (a) status check WITHOUT claiming
    status = await service.check_status(code)
    if status is not MediaStatus.OK:
        return DeliveryResult(_FROM_MEDIA_STATUS[status])

    # (b) force-join gate WITHOUT claiming
    channels = await unjoined_channels(bot, session, user_id)
    if channels:
        return DeliveryResult(DeliveryStatus.GATED, channels=channels)

    # (c) password gate WITHOUT claiming (after force-join, before the claim)
    protected = await service.get_by_code(code)
    if protected is not None and protected.password_hash and not password_verified:
        return DeliveryResult(DeliveryStatus.PASSWORD_REQUIRED, media=protected)

    # (c2) paywall gate WITHOUT claiming (J6): plan / price / free quota
    if protected is not None:
        from app.services.paywall_service import (
            PLAN_REQUIRED,
            PAYMENT_REQUIRED,
            QUOTA_EXCEEDED,
            PaywallService,
        )

        db_user_pre = (
            await UserService(session).get_by_telegram_id(user.id) if user else None
        )
        gate = await PaywallService(session).check_access(
            protected, db_user_pre, user_id
        )
        if gate == PLAN_REQUIRED:
            return DeliveryResult(DeliveryStatus.PLAN_REQUIRED, media=protected)
        if gate == PAYMENT_REQUIRED:
            return DeliveryResult(DeliveryStatus.PAYMENT_REQUIRED, media=protected)
        if gate == QUOTA_EXCEEDED:
            return DeliveryResult(DeliveryStatus.QUOTA_EXCEEDED, media=protected)

    # (d) atomic claim
    claim_status, media = await service.try_claim_download(code)
    if claim_status is not MediaStatus.OK or media is None:
        return DeliveryResult(_FROM_MEDIA_STATUS.get(claim_status, DeliveryStatus.FAILED))

    # best-effort ad before the file (never blocks/fails the delivery)
    await send_placement_ads(bot, session, chat_id, user_id, "before_file")

    # send every file; caption + share button on the first only
    share_markup = build_delivered_actions(
        await service.deep_link(media), media.id,
        likes=media.like_count, dislikes=media.dislike_count,
    )
    # J3: per-tenant caption tools (strip links / signature), applied at delivery
    from app.services.caption_service import apply_caption_tools

    delivered_caption = await apply_caption_tools(session, media.caption)
    sent_ids: list[int] = []
    for index, media_file in enumerate(media.files):
        caption = delivered_caption if index == 0 else None
        reply_markup = share_markup if index == 0 else None
        try:
            message_id = await send_media_file(
                bot,
                chat_id,
                media_file,
                caption=caption,
                protect_content=media.protect_content,
                reply_markup=reply_markup,
                thumbnail=media.thumbnail_file_id,  # J4: custom video cover
            )
            sent_ids.append(message_id)
        except Exception as exc:  # a failed item shouldn't abort the rest
            log.warning("send_failed", media_id=media.id, error=str(exc))

    # nothing delivered -> release the claimed slot
    if not sent_ids:
        await service.release_download(media.id)
        return DeliveryResult(DeliveryStatus.FAILED, media=media)

    db_user = await UserService(session).get_by_telegram_id(user_id) if user else None
    await service.log_download(
        media.id,
        telegram_id=user_id,
        user_id=db_user.id if db_user else None,
    )
    # J6: count this delivery against the free daily quota (atomic Redis INCR)
    from app.services.paywall_service import PaywallService

    await PaywallService(session).count_delivery(db_user)

    if media.auto_delete_seconds and media.auto_delete_seconds > 0:
        from app.core.tenant_context import current_tenant

        await notify_auto_delete(bot, chat_id, media.auto_delete_seconds)
        tid = current_tenant()
        await AutoDeleteQueue(get_redis()).schedule(
            chat_id, sent_ids, media.auto_delete_seconds,
            tenant_id=tid if isinstance(tid, int) else None,
        )

    # best-effort ad after the file
    await send_placement_ads(bot, session, chat_id, user_id, "after_file")

    return DeliveryResult(DeliveryStatus.DELIVERED, media=media, sent_ids=sent_ids)
