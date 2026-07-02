"""In-bot admin extras (G3): secure panel deep-links + in-bot log-group setup.

Secret-heavy work (gateway keys, card number under settings, the stats
dashboard/CSV) is NEVER rebuilt in chat — instead the admin gets short-lived,
single-use, tenant-scoped deep-links to the relevant panel page (PanelLinkService).
The log group (G1) can be connected from chat. Everything is gated by IsAdmin /
IsOwner AND the tenant context, so an admin of tenant A only ever touches
tenant A.
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import messages
from app.bot.filters import IsAdmin, IsOwner
from app.bot.states import LogSetup
from app.core.config import settings
from app.core.redis_client import get_redis
from app.core.tenant_context import require_tenant
from app.models.panel import PanelUser
from app.services.tenant_logger import TenantLogger

router = Router(name="admin_panel")

# secret-heavy / heavy-visual pages that must be reached via the panel, not chat
_LINK_TARGETS = ("providers", "settings", "stats", "dashboard")
_TARGET_PATHS = {
    "providers": "/providers",
    "settings": "/settings",
    "stats": "/stats",
    "dashboard": "",
}


async def _tenant_panel_user_id(session: AsyncSession) -> int | None:
    """The current tenant's panel login (global table, filtered explicitly)."""
    return await session.scalar(
        select(PanelUser.id).where(
            PanelUser.tenant_id == require_tenant(),
            PanelUser.is_active.is_(True),
        ).order_by(PanelUser.id)
    )


@router.message(IsAdmin(), F.text == messages.BTN_PANEL)
async def panel_links(message: Message, state: FSMContext, session: AsyncSession) -> None:
    await state.clear()
    uid = await _tenant_panel_user_id(session)
    if uid is None:
        await message.answer(messages.PANEL_LINK_NO_ACCOUNT)
        return

    from app.panel.link_service import PanelLinkService

    link_svc = PanelLinkService(get_redis())
    tenant_id = require_tenant()
    base = f"{settings.domain.rstrip('/')}{settings.panel_path}"
    rows = []
    for target in _LINK_TARGETS:
        token = await link_svc.mint(
            tenant_id=tenant_id, panel_user_id=uid,
            target=f"{settings.panel_path}{_TARGET_PATHS[target]}",
        )
        rows.append(
            [InlineKeyboardButton(
                text=messages.panel_link_label(target),
                url=f"{base}/link/{token}",
            )]
        )
    await message.answer(
        messages.PANEL_LINK_INTRO,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.message(IsOwner(), F.text == messages.BTN_LOG_GROUP)
async def log_group_start(message: Message, state: FSMContext) -> None:
    await state.set_state(LogSetup.waiting_group)
    await message.answer(messages.LOG_GROUP_ASK)


@router.message(IsOwner(), LogSetup.waiting_group, F.text)
async def log_group_input(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    raw = (message.text or "").strip()
    await state.clear()
    if not raw.lstrip("-").isdigit():
        await message.answer(messages.LOG_GROUP_INVALID)
        return
    group_id = int(raw)
    await TenantLogger(session).set_group(group_id or None)
    await message.answer(
        messages.LOG_GROUP_CLEARED if group_id == 0 else messages.LOG_GROUP_SET
    )
