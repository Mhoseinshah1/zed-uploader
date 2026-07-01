"""Force-join channel management (owners only)."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import messages
from app.bot.callbacks import ChanCb
from app.bot.filters import IsOwner
from app.bot.keyboards.inline import build_channels_list
from app.bot.states import ChannelAdd
from app.core.logging import get_logger
from app.services.channel_service import ChannelService

router = Router(name="channels")
log = get_logger("handler.channels")


async def _show_channels(target: Message, session: AsyncSession) -> None:
    channels = await ChannelService(session).list_all()
    text = messages.CHANNELS_HEADER if channels else messages.CHANNELS_EMPTY
    await target.answer(text, reply_markup=build_channels_list(channels))


@router.message(IsOwner(), F.text == messages.BTN_CHANNELS)
async def channels_menu(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    await state.clear()
    await _show_channels(message, session)


@router.callback_query(IsOwner(), ChanCb.filter(F.action == "toggle"))
async def channel_toggle(
    callback: CallbackQuery, callback_data: ChanCb, session: AsyncSession
) -> None:
    await ChannelService(session).toggle(callback_data.id)
    log.info("channel_toggled", id=callback_data.id)
    if isinstance(callback.message, Message):
        channels = await ChannelService(session).list_all()
        try:
            await callback.message.edit_reply_markup(
                reply_markup=build_channels_list(channels)
            )
        except Exception:
            pass
    await callback.answer()


@router.callback_query(IsOwner(), ChanCb.filter(F.action == "remove"))
async def channel_remove(
    callback: CallbackQuery, callback_data: ChanCb, session: AsyncSession
) -> None:
    await ChannelService(session).remove(callback_data.id)
    log.info("channel_removed", id=callback_data.id)
    if isinstance(callback.message, Message):
        channels = await ChannelService(session).list_all()
        try:
            await callback.message.edit_reply_markup(
                reply_markup=build_channels_list(channels)
            )
        except Exception:
            pass
    await callback.answer(messages.CHANNEL_REMOVED)


@router.callback_query(IsOwner(), ChanCb.filter(F.action == "add"))
async def channel_add_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(ChannelAdd.waiting)
    if isinstance(callback.message, Message):
        await callback.message.answer(messages.ASK_CHANNEL)
    await callback.answer()


@router.message(IsOwner(), ChannelAdd.waiting)
async def channel_add_input(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    text = (message.text or "").strip()
    forwarded = message.forward_from_chat

    chat_ref: str | None = None
    title: str | None = None
    if text.startswith("@"):
        chat_ref = text.split()[0]
    elif forwarded is not None and forwarded.type == "channel":
        chat_ref = f"@{forwarded.username}" if forwarded.username else str(forwarded.id)
        title = forwarded.title

    if not chat_ref:
        await message.answer(messages.CHANNEL_INVALID)
        return

    invite_link: str | None = None
    not_admin = False
    try:
        chat = await message.bot.get_chat(chat_ref)
        title = title or chat.title
        invite_link = getattr(chat, "invite_link", None)
        try:
            me = await message.bot.me()
            member = await message.bot.get_chat_member(chat_ref, me.id)
            if member.status not in ("administrator", "creator"):
                not_admin = True
        except Exception:
            not_admin = True
    except Exception:
        await message.answer(messages.CHANNEL_INVALID)
        return

    await ChannelService(session).add(chat_ref, title=title, invite_link=invite_link)
    await state.clear()
    log.info("channel_added", chat_id=chat_ref)
    reply = messages.CHANNEL_ADDED
    if not_admin:
        reply = f"{reply}\n{messages.CHANNEL_NOT_ADMIN_WARN}"
    await message.answer(reply)
    await _show_channels(message, session)
