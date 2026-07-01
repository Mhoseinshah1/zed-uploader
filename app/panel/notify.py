"""Best-effort Telegram notifications from the panel (review approve/reject).

The panel runs inside the API process and has no long-lived Bot, so it spins up
a short-lived one per notification. Failures are swallowed — a delivery hiccup
must never break an admin's review action.
"""
from __future__ import annotations

from app.bot.factory import create_bot
from app.core.logging import get_logger

log = get_logger("panel.notify")


async def notify_user(telegram_id: int | None, text: str) -> None:
    if not telegram_id:
        return
    bot = create_bot()
    try:
        await bot.send_message(telegram_id, text)
    except Exception as exc:  # blocked bot / network / never-started chat
        log.warning("panel_notify_failed", telegram_id=telegram_id, error=str(exc))
    finally:
        try:
            await bot.session.close()
        except Exception:
            pass
