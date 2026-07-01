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
            now_m = time.monotonic()
            if now_m - last_sweep >= EXPIRY_SWEEP_INTERVAL:
                last_sweep = now_m
                try:
                    expired = await process_expiry_sweep(async_session_maker)
                    if expired:
                        log.info("plans_expired", count=expired)
                except Exception as exc:
                    log.warning("expiry_sweep_error", error=str(exc))
            await asyncio.sleep(POLL_INTERVAL)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
