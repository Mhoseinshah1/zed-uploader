"""Worker: auto-delete queue (Section 6.2) + broadcast queue (Phase 2).

Single-instance only: pop_due->ack and the broadcast cursor are not safe with
multiple worker replicas. Must never crash on an already-deleted message.
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone

from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from sqlalchemy import select, update

from app.bot import messages
from app.bot.factory import create_bot
from app.core.logging import get_logger, setup_logging
from app.core.redis_client import get_redis
from app.db.session import async_session_maker
from app.models.subscription import Subscription
from app.models.user import User
from app.services import broadcast as bcast
from app.services.autodelete import AutoDeleteQueue

log = get_logger("worker")
POLL_INTERVAL = 2
EXPIRY_SWEEP_INTERVAL = 60


async def process_once(bot, queue) -> int:
    members = await queue.pop_due()
    if not members:
        return 0
    for raw in members:
        try:
            d = json.loads(raw)
            await bot.delete_message(chat_id=d["c"], message_id=d["m"])
        except TelegramBadRequest:
            pass
        except Exception as exc:
            log.warning("delete_failed", error=str(exc))
    await queue.ack(members)
    return len(members)


async def process_broadcast_once(bot, session_maker) -> bool:
    """Process ONE page of the oldest unfinished broadcast job.

    Exactly-once & resumable: recipients live in a DB ledger. Each row moves off
    ``pending`` the moment it is attempted, so a crash/restart re-reads only the
    rows still ``pending`` and never re-sends a delivered one. Returns True while
    there is broadcast work (so the caller can keep draining), False when idle.
    """
    async with session_maker() as session:
        job = await bcast.claim_next_job(session)
        if job is None:
            return False
        job_id = job.id
        from_chat_id, message_id, text, created_by = (
            job.from_chat_id, job.message_id, job.text, job.created_by,
        )

        recipients = await bcast.next_pending_page(session, job_id, bcast.PAGE_SIZE)
        if not recipients:
            sent, failed, blocked = await bcast.finalize_job(session, job_id)
            log.info("broadcast_done", job_id=job_id, sent=sent, failed=failed, blocked=blocked)
            if created_by:
                try:
                    await bot.send_message(
                        created_by, messages.broadcast_summary(sent, failed, blocked)
                    )
                except Exception as exc:
                    log.warning("broadcast_summary_failed", error=str(exc))
            return True

        now = datetime.now(timezone.utc)
        for recipient in recipients:
            outcome, error = await _send_one(
                bot, from_chat_id, message_id, text, recipient.telegram_id
            )
            recipient.status = outcome
            recipient.error_message = error
            if outcome == "sent":
                recipient.sent_at = now
            elif outcome == "blocked":
                await session.execute(
                    update(User)
                    .where(User.id == recipient.user_id)
                    .values(is_blocked=True)
                )
            # Commit each recipient's outcome before the next send, so a crash
            # re-sends at most the one in-flight message (never a whole page).
            await session.commit()
            await asyncio.sleep(bcast.SEND_DELAY)
        await bcast.refresh_job_counts(session, job_id)
        await session.commit()
    return True


async def _deliver(bot, from_chat_id, message_id, text, telegram_id) -> None:
    # Panel text broadcasts carry "text"; bot broadcasts carry a message to copy.
    if text is not None:
        await bot.send_message(telegram_id, text)
    else:
        await bot.copy_message(
            chat_id=telegram_id, from_chat_id=from_chat_id, message_id=message_id
        )


async def _send_one(bot, from_chat_id, message_id, text, telegram_id) -> tuple[str, str | None]:
    """Send to one recipient. Returns (status, error_message).

    Forbidden/deactivated -> 'blocked'; RetryAfter -> back off and retry once;
    any other error -> 'failed' with a truncated message.
    """
    try:
        await _deliver(bot, from_chat_id, message_id, text, telegram_id)
        return "sent", None
    except TelegramRetryAfter as exc:
        await asyncio.sleep(exc.retry_after)
        try:
            await _deliver(bot, from_chat_id, message_id, text, telegram_id)
            return "sent", None
        except Exception as exc2:
            log.warning("broadcast_send_failed", error=str(exc2))
            return "failed", str(exc2)[:255]
    except TelegramForbiddenError as exc:
        return "blocked", str(exc)[:255]
    except TelegramBadRequest as exc:
        return "failed", str(exc)[:255]
    except Exception as exc:
        log.warning("broadcast_send_failed", error=str(exc))
        return "failed", str(exc)[:255]


async def process_albums_once(bot, redis, session_maker) -> int:
    """Finalize any albums whose debounce elapsed. Returns how many finalized."""
    from app.services.album_buffer import AlbumBuffer

    groups = await AlbumBuffer(redis).pop_due(time.time())
    for group in groups:
        try:
            await _finalize_album(bot, session_maker, group)
        except Exception as exc:
            log.warning("album_finalize_failed", gk=group.get("group_key"), error=str(exc))
    return len(groups)


async def _finalize_album(bot, session_maker, group) -> None:
    from app.bot.gating import within_file_limit
    from app.services.admin_service import AdminService
    from app.services.bot_setting_service import BotSettingService
    from app.services.media_service import MediaService
    from app.services.plan_service import PlanService
    from app.services.user_service import UserService

    parts = group.get("parts") or []
    if not parts:
        return
    chat_id = group["chat_id"]
    telegram_id = group["telegram_id"]
    files = [p["file"] for p in parts]
    caption = parts[0].get("caption")  # first item's caption is the media caption

    async with session_maker() as session:
        is_admin = await AdminService.is_admin(session, telegram_id)
        setting = BotSettingService(session)
        user = await UserService(session).get_by_telegram_id(telegram_id)

        if is_admin:
            status = "approved"
        else:
            if not await setting.user_upload_enabled():
                await _notify(bot, chat_id, messages.NOT_ADMIN_UPLOAD)
                return
            status = "pending" if await setting.user_upload_requires_review() else "approved"
            if user is not None and not await within_file_limit(session, user, telegram_id):
                limit = await PlanService(session).max_files(user.effective_plan)
                await _notify(bot, chat_id, messages.file_limit_reached(limit or 0))
                return

        service = MediaService(session)
        media = await service.create_media(
            files=files,
            owner_user_id=user.id if user else None,
            caption=caption,
            protect_content=await setting.effective_protect(),
            auto_delete_seconds=(await setting.effective_autodelete()) or None,
            status=status,
        )
        link = service.deep_link(media)
        code, count = media.code, len(files)

    log.info("album_finalized", chat_id=chat_id, count=count, status=status)
    if status == "approved":
        await _notify(bot, chat_id, messages.batch_done(link, code, count))
    else:
        await _notify(bot, chat_id, messages.UPLOAD_PENDING_REVIEW)


async def _notify(bot, chat_id: int, text: str) -> None:
    try:
        await bot.send_message(chat_id, text)
    except Exception as exc:
        log.warning("album_notify_failed", chat_id=chat_id, error=str(exc))


async def process_backups_once(session_maker) -> int:
    """Create a due scheduled backup, then run ONE pending job (if any).

    Prunes old success backups after each successful run (keep-N from settings).
    """
    from app.services.backup_service import (
        DEFAULT_BACKUP_KEEP,
        KEY_BACKUP_KEEP,
        KEY_BACKUP_SCHEDULE,
        BackupService,
    )
    from app.services.bot_setting_service import BotSettingService

    async with session_maker() as session:
        svc = BackupService(session)
        setting = BotSettingService(session)
        schedule = (await setting.get_raw(KEY_BACKUP_SCHEDULE)) or "off"
        if await svc.due_scheduled(schedule):
            await svc.create_job(type_="scheduled")
            log.info("backup_scheduled")
        job = await svc.next_pending()
        if job is None:
            return 0
        await svc.run_job(job)
        if job.status == "success":
            keep = await setting.get_int(KEY_BACKUP_KEEP, DEFAULT_BACKUP_KEEP)
            await svc.prune(keep)
        return 1


async def process_expiry_sweep(session_maker) -> int:
    """Downgrade users whose paid plan has expired; deactivate their subs."""
    async with session_maker() as session:
        now = datetime.now(timezone.utc)
        users = list(
            await session.scalars(
                select(User).where(
                    User.plan != "free",
                    User.plan_expires_at.is_not(None),
                    User.plan_expires_at < now,
                )
            )
        )
        if not users:
            return 0
        ids = [u.id for u in users]
        for u in users:
            u.plan = "free"
            log.info("plan_expired", user_id=u.id)
        await session.execute(
            update(Subscription)
            .where(Subscription.user_id.in_(ids), Subscription.is_active.is_(True))
            .values(is_active=False)
        )
        await session.commit()
        return len(users)


async def main() -> None:
    setup_logging()
    bot = create_bot()
    redis = get_redis()
    queue = AutoDeleteQueue(redis)
    last_sweep = 0.0
    log.info("worker_started")
    try:
        while True:
            try:
                n = await process_once(bot, queue)
                if n:
                    log.info("auto_deleted", count=n)
            except Exception as exc:
                log.warning("worker_loop_error", error=str(exc))
            try:
                await process_broadcast_once(bot, async_session_maker)
            except Exception as exc:
                log.warning("broadcast_loop_error", error=str(exc))
            try:
                n = await process_albums_once(bot, redis, async_session_maker)
                if n:
                    log.info("albums_finalized", count=n)
            except Exception as exc:
                log.warning("album_loop_error", error=str(exc))
            now_m = time.monotonic()
            if now_m - last_sweep >= EXPIRY_SWEEP_INTERVAL:
                last_sweep = now_m
                try:
                    expired = await process_expiry_sweep(async_session_maker)
                    if expired:
                        log.info("plans_expired", count=expired)
                except Exception as exc:
                    log.warning("expiry_sweep_error", error=str(exc))
                try:
                    await process_backups_once(async_session_maker)
                except Exception as exc:
                    log.warning("backup_loop_error", error=str(exc))
            await asyncio.sleep(POLL_INTERVAL)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
