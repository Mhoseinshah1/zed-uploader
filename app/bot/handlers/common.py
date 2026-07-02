"""Catch-all fallback handler.

Registered LAST in the dispatcher so it never shadows /start or the upload
handlers. Any other message (plain text, /help, unknown commands) gets a short
Persian help reply.
"""
from __future__ import annotations

from aiogram import Router
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.text_service import get_text

router = Router(name="common")


@router.message()
async def fallback(message: Message, session: AsyncSession) -> None:
    await message.answer(await get_text(session, "help"))
