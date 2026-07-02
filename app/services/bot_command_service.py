"""Scope-based Telegram command menu: DB rows -> built-in Persian defaults.

Two audiences ("scopes"): ``user`` — the list every normal user sees behind
the blue Menu button (BotCommandScopeDefault) — and ``admin`` — the full list
pushed per-admin chat (BotCommandScopeChat), since Telegram has no built-in
bot-admin scope. Entries are editable in the panel; while a scope has no rows
the built-in defaults apply. Resolution is Redis-cached with a short TTL (like
text_service) and cache-busted on every save, and the panel re-pushes to
Telegram on save so edits take effect immediately.
"""
from __future__ import annotations

import json
import re

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis_client import get_redis
from app.models.bot_command import BotCommandEntry

CACHE_TTL = 60  # seconds — worst-case staleness of a resolved list
SCOPES = ("user", "admin")
MAX_DESCRIPTION = 256  # Telegram's BotCommand.description limit
MAX_COMMANDS_PER_SCOPE = 100  # Telegram rejects setMyCommands beyond 100
_MAX_SORT = 2**31 - 1  # the column is int4; reject instead of a DB error
# Telegram command-name rules: lowercase a-z, digits, underscore; 1-32 chars.
COMMAND_RE = re.compile(r"[a-z0-9_]{1,32}")

# scope -> [(command, Persian description)] — the panel shows these as the
# built-in defaults; they apply verbatim while a scope has no rows.
DEFAULT_COMMANDS: dict[str, list[tuple[str, str]]] = {
    "user": [
        ("start", "شروع ربات و دریافت فایل"),
        ("help", "راهنما"),
        ("buy", "خرید پلن و اشتراک"),
        ("wallet", "کیف پول و شارژ حساب"),
        ("myfiles", "فایل‌های من"),
        ("search", "جستجوی فایل‌ها"),
        ("report", "راهنمای گزارش محتوا"),
    ],
    "admin": [
        ("panel", "پنل مدیریت"),
        ("upload", "آپلود فایل"),
        ("myfiles", "فایل‌های من"),
        ("folders", "پوشه‌ها"),
        ("stats", "آمار"),
        ("review", "صف بازبینی آپلودها"),
        ("search", "جستجوی فایل‌ها"),
        ("broadcast", "ارسال پیام همگانی (مالک)"),
        ("backup", "پشتیبان‌گیری (مالک)"),
        ("ads", "تبلیغات (مالک)"),
        ("wallet", "کیف پول"),
        ("buy", "خرید پلن و اشتراک"),
        ("help", "راهنما"),
    ],
}


def _cache_key(scope: str) -> str:
    return f"botcmds:{scope}"


def valid_command(command: str) -> bool:
    return bool(COMMAND_RE.fullmatch(command))


async def bust_cache(scope: str) -> None:
    try:
        await get_redis().delete(_cache_key(scope))
    except Exception:
        pass  # worst case: the old list lives for CACHE_TTL more seconds


async def resolved_commands(session: AsyncSession, scope: str) -> list[tuple[str, str]]:
    """Effective (command, description) list: cache -> active rows -> defaults.

    The user scope drops ``search`` while public search is disabled, so the
    menu never advertises a command that would only answer "disabled".
    """
    if scope not in SCOPES:
        return []
    try:
        cached = await get_redis().get(_cache_key(scope))
        if cached:
            return [(c, d) for c, d in json.loads(cached)]
    except Exception:
        pass  # Redis down -> fall through to DB/defaults

    rows = await BotCommandService(session).list_rows(scope)
    if rows:
        pairs = [(r.command, r.description) for r in rows if r.is_active]
    else:
        pairs = list(DEFAULT_COMMANDS[scope])
    if scope == "user":
        from app.services.bot_setting_service import BotSettingService

        if not await BotSettingService(session).public_search_enabled():
            pairs = [p for p in pairs if p[0] != "search"]
    try:
        await get_redis().set(_cache_key(scope), json.dumps(pairs), ex=CACHE_TTL)
    except Exception:
        pass
    return pairs


class BotCommandService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_rows(self, scope: str) -> list[BotCommandEntry]:
        result = await self.session.scalars(
            select(BotCommandEntry)
            .where(BotCommandEntry.scope == scope)
            .order_by(BotCommandEntry.sort_order, BotCommandEntry.id)
        )
        return list(result.all())

    async def get(self, entry_id: int) -> BotCommandEntry | None:
        return await self.session.scalar(
            select(BotCommandEntry).where(BotCommandEntry.id == entry_id)
        )

    @staticmethod
    def _clean_description(description: str) -> str | None:
        cleaned = description.strip()
        return cleaned if cleaned and len(cleaned) <= MAX_DESCRIPTION else None

    @staticmethod
    def _valid_sort(sort_order: int) -> bool:
        return -(2**31) <= sort_order <= _MAX_SORT

    async def upsert(
        self,
        scope: str,
        command: str,
        description: str,
        sort_order: int = 0,
        is_active: bool = True,
    ) -> BotCommandEntry | None:
        """Add or update one entry. Returns None (no write) on invalid input."""
        command = command.strip().lstrip("/")
        cleaned = self._clean_description(description)
        if (
            scope not in SCOPES
            or not valid_command(command)
            or cleaned is None
            or not self._valid_sort(sort_order)
        ):
            return None
        row = await self.session.scalar(
            select(BotCommandEntry).where(
                BotCommandEntry.scope == scope, BotCommandEntry.command == command
            )
        )
        if row is None:
            # stay under Telegram's per-scope limit, or every future push fails
            if len(await self.list_rows(scope)) >= MAX_COMMANDS_PER_SCOPE:
                return None
            row = BotCommandEntry(
                scope=scope, command=command, description=cleaned,
                sort_order=sort_order, is_active=is_active,
            )
            self.session.add(row)
        else:
            row.description = cleaned
            row.sort_order = sort_order
            row.is_active = is_active
        try:
            await self.session.commit()
        except IntegrityError:
            # concurrent add of the same (scope, command): update the winner
            await self.session.rollback()
            row = await self.session.scalar(
                select(BotCommandEntry).where(
                    BotCommandEntry.scope == scope, BotCommandEntry.command == command
                )
            )
            if row is None:
                return None
            row.description = cleaned
            row.sort_order = sort_order
            row.is_active = is_active
            await self.session.commit()
        await bust_cache(scope)
        return row

    async def update(
        self, entry_id: int, description: str, sort_order: int, is_active: bool
    ) -> BotCommandEntry | None:
        row = await self.get(entry_id)
        cleaned = self._clean_description(description)
        if row is None or cleaned is None or not self._valid_sort(sort_order):
            return None
        row.description = cleaned
        row.sort_order = sort_order
        row.is_active = is_active
        await self.session.commit()
        await bust_cache(row.scope)
        return row

    async def remove(self, entry_id: int) -> BotCommandEntry | None:
        """Delete one entry; returns it (for its scope) or None if missing."""
        row = await self.get(entry_id)
        if row is None:
            return None
        scope = row.scope
        await self.session.delete(row)
        await self.session.commit()
        await bust_cache(scope)
        return row

    async def seed_defaults(self, scope: str) -> int:
        """Materialize the built-in defaults as editable rows (only when empty)."""
        if scope not in SCOPES or await self.list_rows(scope):
            return 0
        for index, (command, description) in enumerate(DEFAULT_COMMANDS[scope]):
            self.session.add(
                BotCommandEntry(
                    scope=scope, command=command, description=description,
                    sort_order=index,
                )
            )
        try:
            await self.session.commit()
        except IntegrityError:
            await self.session.rollback()  # concurrent seed already materialized
            return 0
        await bust_cache(scope)
        return len(DEFAULT_COMMANDS[scope])

    async def reset(self, scope: str) -> None:
        """Drop every row of a scope -> the built-in defaults apply again."""
        await self.session.execute(
            delete(BotCommandEntry).where(BotCommandEntry.scope == scope)
        )
        await self.session.commit()
        await bust_cache(scope)
