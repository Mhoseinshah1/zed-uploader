"""Auto-delete worker (Section 6.2).

Polls the Redis ZSET for due deletions and removes the messages. Must never
crash on an already-deleted message.
"""
from __future__ import annotations

import asyncio
import json

from aiogram.exceptions import TelegramBadRequest

from app.bot.factory import create_bot
from app.core.logging import get_logger, setup_logging
from app.core.redis_client import get_redis
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


async def main() -> None:
    setup_logging()
    bot = create_bot()
    queue = AutoDeleteQueue(get_redis())
    log.info("worker_started")
    try:
        while True:
            try:
                n = await process_once(bot, queue)
                if n:
                    log.info("auto_deleted", count=n)
            except Exception as exc:
                log.warning("worker_loop_error", error=str(exc))
            await asyncio.sleep(POLL_INTERVAL)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
