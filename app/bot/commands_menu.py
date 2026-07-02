"""Push the scope-based command menu to Telegram (best-effort, never raises).

Telegram has no "bot admins" scope, so: the user list is set on
BotCommandScopeDefault (+ AllPrivateChats), and the admin list is pushed
per-admin chat via BotCommandScopeChat — re-applied on startup, whenever an
admin interacts (/start, /panel) and eagerly when the panel saves an edit or
adds an admin. Removing an admin deletes their chat-scoped list, so they fall
back to the user menu. Every Telegram call is wrapped so a hiccup never
crashes startup or a handler.
"""
from __future__ import annotations

import asyncio

from aiogram.types import (
    BotCommand,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeChat,
    BotCommandScopeDefault,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.services.bot_command_service import resolved_commands

log = get_logger("commands_menu")


def _as_bot_commands(pairs: list[tuple[str, str]]) -> list[BotCommand]:
    return [BotCommand(command=c, description=d) for c, d in pairs]


async def push_default_commands(bot, session: AsyncSession) -> bool:
    """Set the user list for everyone (default scope + all private chats)."""
    try:
        commands = _as_bot_commands(await resolved_commands(session, "user"))
        await bot.set_my_commands(commands, scope=BotCommandScopeDefault())
        await bot.set_my_commands(commands, scope=BotCommandScopeAllPrivateChats())
        return True
    except Exception as exc:
        log.warning("commands_push_default_failed", error=str(exc))
        return False


async def push_admin_commands(bot, session: AsyncSession, chat_id: int) -> bool:
    """Give one admin's private chat the full admin list."""
    try:
        commands = _as_bot_commands(await resolved_commands(session, "admin"))
        await bot.set_my_commands(commands, scope=BotCommandScopeChat(chat_id=chat_id))
        return True
    except Exception as exc:
        log.warning("commands_push_admin_failed", chat_id=chat_id, error=str(exc))
        return False


async def push_admin_commands_many(
    bot, session: AsyncSession, chat_ids: list[int]
) -> None:
    """Push the admin list to many chats: resolve once, send concurrently.

    The session is used exactly once (before the fan-out) — an AsyncSession
    must never be shared by concurrent tasks.
    """
    try:
        commands = _as_bot_commands(await resolved_commands(session, "admin"))
    except Exception as exc:
        log.warning("commands_push_admin_failed", error=str(exc))
        return

    async def _one(chat_id: int) -> None:
        try:
            await bot.set_my_commands(
                commands, scope=BotCommandScopeChat(chat_id=chat_id)
            )
        except Exception as exc:
            log.warning("commands_push_admin_failed", chat_id=chat_id, error=str(exc))

    await asyncio.gather(*(_one(chat_id) for chat_id in chat_ids))


async def clear_admin_commands(bot, chat_id: int) -> bool:
    """Removed/deactivated admin -> drop the chat-scoped list (back to default)."""
    try:
        await bot.delete_my_commands(scope=BotCommandScopeChat(chat_id=chat_id))
        return True
    except Exception as exc:
        log.warning("commands_clear_failed", chat_id=chat_id, error=str(exc))
        return False


async def sync_all(bot, session: AsyncSession) -> None:
    """Startup: (re)apply the default list + the admin list for every admin."""
    from app.services.admin_service import AdminService

    await push_default_commands(bot, session)
    chat_ids = await AdminService.admin_telegram_ids(session)
    await push_admin_commands_many(bot, session, chat_ids)
