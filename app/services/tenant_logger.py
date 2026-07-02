"""TenantLogger — per-tenant Telegram forum-topic logging (G1).

Streams structured, REDACTED operational events to the current tenant's log
group, one topic per category. Everything is best-effort and non-blocking: an
unset group is a silent no-op, and a Telegram failure never propagates to the
caller (payments/delivery/uploads must never break). After a failure a short
Redis backoff suppresses further attempts so a broken group can't spam retries.

Card numbers are masked and gateway keys/bot tokens are NEVER included — the
callers pass already-safe strings, and helpers here mask on the way in.
"""
from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.redis_client import get_redis
from app.core.tenant_context import require_tenant
from app.models.tenant_log import TenantLogSettings

log = get_logger("tenant_logger")

# category -> (settings column, Persian topic title)
CATEGORIES: dict[str, tuple[str, str]] = {
    "payments": ("topic_payments", "پرداخت‌ها"),
    "uploads": ("topic_uploads", "آپلودها"),
    "errors": ("topic_errors", "خطاها"),
    "new_users": ("topic_new_users", "کاربران جدید"),
    "backups": ("topic_backups", "بکاپ"),
}

FAIL_BACKOFF_TTL = 300  # seconds a broken group is skipped after a send failure


def mask_card(number: str | None) -> str:
    """6037991122334455 -> 6037••••4455. Never reveal the middle digits."""
    if not number:
        return "?"
    digits = re.sub(r"\D", "", number)
    if len(digits) < 8:
        return "•" * len(digits)
    return f"{digits[:4]}••••{digits[-4:]}"


class TenantLogger:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_settings(self) -> TenantLogSettings | None:
        """The current tenant's row (guard-filtered to this tenant)."""
        return await self.session.scalar(select(TenantLogSettings))

    async def set_group(self, group_id: int | None) -> TenantLogSettings:
        """Connect/replace the log group; clears topic ids so they re-create."""
        row = await self.get_settings()
        if row is None:
            row = TenantLogSettings(log_group_id=group_id)
            self.session.add(row)
        else:
            row.log_group_id = group_id
        row.topic_payments = None
        row.topic_uploads = None
        row.topic_errors = None
        row.topic_new_users = None
        row.topic_backups = None
        await self.session.commit()
        return row

    async def _ensure_topic(self, bot, row: TenantLogSettings, category: str) -> int | None:
        """Return the thread id for a category, creating the forum topic once."""
        column, title = CATEGORIES[category]
        thread_id = getattr(row, column)
        if thread_id is not None:
            return thread_id
        topic = await bot.create_forum_topic(chat_id=row.log_group_id, name=title)
        thread_id = topic.message_thread_id
        setattr(row, column, thread_id)
        await self.session.commit()
        return thread_id

    async def _backoff_key(self) -> str:
        return f"logfail:{require_tenant()}"

    async def emit(self, category: str, text: str, bot=None) -> bool:
        """Route one event to the tenant's topic. Best-effort; never raises."""
        if category not in CATEGORIES:
            return False
        try:
            row = await self.get_settings()
            if row is None or not row.log_group_id:
                return False  # logging not configured -> silent no-op
            redis = get_redis()
            try:
                if await redis.get(await self._backoff_key()):
                    return False  # a recent failure -> skip to avoid spamming
            except Exception:
                pass

            own_bot = False
            if bot is None:
                bot = await self._bot_for_tenant()
                own_bot = bot is not None
            if bot is None:
                return False
            try:
                thread_id = await self._ensure_topic(bot, row, category)
                await bot.send_message(
                    chat_id=row.log_group_id, text=text, message_thread_id=thread_id
                )
                return True
            finally:
                if own_bot:
                    try:
                        await bot.session.close()
                    except Exception:
                        pass
        except Exception as exc:
            log.warning("tenant_log_failed", category=category, error=str(exc))
            try:
                await get_redis().set(await self._backoff_key(), "1", ex=FAIL_BACKOFF_TTL)
            except Exception:
                pass
            return False

    async def _bot_for_tenant(self):
        """Build a throwaway Bot from the current tenant's token (panel/worker
        callers with no bot in hand). Returns None if unavailable."""
        try:
            from aiogram import Bot

            from app.models.tenant import Tenant
            from app.services.tenant_service import TenantService

            tenant = await self.session.scalar(
                select(Tenant).where(Tenant.id == require_tenant())
            )
            token = TenantService.decrypt_token(tenant) if tenant else None
            return Bot(token=token) if token else None
        except Exception:
            return None

    # --- convenience emitters ------------------------------------------------
    async def log_new_user(self, telegram_id: int, name: str | None, bot=None) -> None:
        await self.emit(
            "new_users", f"👤 کاربر جدید: {name or '—'} (id {telegram_id})", bot=bot
        )

    async def log_upload(self, code: str, telegram_id: int, count: int = 1, bot=None) -> None:
        await self.emit(
            "uploads", f"📤 آپلود جدید: کد {code} ({count} فایل) توسط {telegram_id}", bot=bot
        )

    async def log_payment(
        self, *, method: str, amount: int, ref: str | None = None,
        status: str = "approved", card: str | None = None, bot=None,
    ) -> None:
        parts = [f"💳 پرداخت {status}", f"روش: {method}", f"مبلغ: {amount:,}"]
        if card:
            parts.append(f"کارت: {mask_card(card)}")
        if ref:
            parts.append(f"کد: {ref}")
        await self.emit("payments", " | ".join(parts), bot=bot)

    async def log_plan_purchase(self, plan_key: str, amount: int, telegram_id: int, bot=None) -> None:
        await self.emit(
            "payments", f"⭐️ خرید پلن {plan_key} ({amount:,}) توسط {telegram_id}", bot=bot
        )

    async def log_error(self, context: str, message: str, bot=None) -> None:
        await self.emit("errors", f"⚠️ خطا [{context}]: {message[:400]}", bot=bot)

    async def log_backup(self, note: str, bot=None) -> None:
        await self.emit("backups", f"🗄 {note}", bot=bot)
