"""Worker: auto-delete queue (Section 6.2) + broadcast queue (Phase 2).

Single-instance only: pop_due->ack and the broadcast cursor are not safe with
multiple worker replicas. Must never crash on an already-deleted message.
"""
from __future__ import annotations

import asyncio
import json

from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from sqlalchemy import select

from app.bot import messages
from app.bot.factory import create_bot
from app.core.logging import get_logger, setup_logging
from app.core.redis_client import get_redis
from app.db.session import async_session_maker
from app.models.user import User
from app.services import broadcast as bcast
from app.services.autodelete import AutoDeleteQueue

log = get_logger("worker")
POLL_INTERVAL = 2


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


async def process_broadcast_once(bot, redis, session_maker) -> bool:
    """Process ONE page of the active broadcast job (resumable per page).

    Picks up the in-progress job (ACTIVE_KEY) or the next queued job, sends to
    one page of users, persists the cursor, and on completion notifies the
    requester. At-least-once: a crash mid-page may re-send a few users.
    """
    active = await redis.get(bcast.ACTIVE_KEY)
    if active is None:
        active = await redis.lpop(bcast.QUEUE_KEY)
        if active is None:
            return False
    job = json.loads(active)
    cursor = int(job.get("cursor_id", 0))

    async with session_maker() as session:
        users = list(
            await session.scalars(
                select(User)
                .where(User.id > cursor)
                .order_by(User.id)
                .limit(bcast.PAGE_SIZE)
            )
        )
        if not users:
            await redis.delete(bcast.ACTIVE_KEY)
            try:
                await bot.send_message(
                    job["requested_by"],
                    messages.broadcast_summary(
                        job.get("sent", 0), job.get("failed", 0)
                    ),
                )
            except Exception as exc:
                log.warning("broadcast_summary_failed", error=str(exc))
            log.info(
                "broadcast_done", sent=job.get("sent", 0), failed=job.get("failed", 0)
            )
            return True

        for user in users:
            result = await _send_one(bot, job, user)
            if result == "blocked":
                user.is_blocked = True
            job["cursor_id"] = user.id
            await asyncio.sleep(bcast.SEND_DELAY)
        await session.commit()

    await redis.set(bcast.ACTIVE_KEY, json.dumps(job))
    return True


async def _copy(bot, job, user) -> None:
    await bot.copy_message(
        chat_id=user.telegram_id,
        from_chat_id=job["from_chat_id"],
        message_id=job["message_id"],
    )


async def _send_one(bot, job, user) -> str:
    """copy_message to one user. Returns 'sent' | 'blocked' | 'failed'.

    Only Forbidden/BadRequest (blocked/deactivated) mark the user blocked;
    transient errors are just counted as failed.
    """
    try:
        await _copy(bot, job, user)
        job["sent"] = job.get("sent", 0) + 1
        return "sent"
    except TelegramRetryAfter as exc:
        await asyncio.sleep(exc.retry_after)
        try:
            await _copy(bot, job, user)
            job["sent"] = job.get("sent", 0) + 1
            return "sent"
        except Exception:
            job["failed"] = job.get("failed", 0) + 1
            return "failed"
    except (TelegramForbiddenError, TelegramBadRequest):
        job["failed"] = job.get("failed", 0) + 1
        return "blocked"
    except Exception as exc:
        log.warning("broadcast_send_failed", error=str(exc))
        job["failed"] = job.get("failed", 0) + 1
        return "failed"


async def main() -> None:
    setup_logging()
    bot = create_bot()
    redis = get_redis()
    queue = AutoDeleteQueue(redis)
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
                await process_broadcast_once(bot, redis, async_session_maker)
            except Exception as exc:
                log.warning("broadcast_loop_error", error=str(exc))
            await asyncio.sleep(POLL_INTERVAL)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
