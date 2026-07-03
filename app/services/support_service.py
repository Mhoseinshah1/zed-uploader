"""Support ticket service (H2).

Tenant-scoped CRUD for tickets + messages, plus best-effort notification helpers
(DM the opener when an admin replies; DM the tenant's admins when a user writes).
Every write goes through the tenant guard: under a tenant context rows are
stamped/filtered automatically; the super-admin platform inbox runs under
ALL_TENANTS, so message inserts there set ``tenant_id`` explicitly from the
ticket (``require_tenant`` would otherwise raise).
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.support import SupportTicket, TicketMessage
from app.models.user import User

log = get_logger("support")

ACTIVE = ("open", "answered")


class SupportService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ---- open / continue ---------------------------------------------------
    async def active_ticket_for(
        self, opener_user_id: int, target: str
    ) -> SupportTicket | None:
        """The opener's current open/answered ticket of this target (if any)."""
        return await self.session.scalar(
            select(SupportTicket)
            .where(
                SupportTicket.opener_user_id == opener_user_id,
                SupportTicket.target == target,
                SupportTicket.status.in_(ACTIVE),
            )
            .order_by(SupportTicket.id.desc())
        )

    async def open_ticket(
        self, opener_user_id: int, subject: str, first_body: str, target: str
    ) -> SupportTicket:
        """Create a ticket + its first (user) message. Tenant stamped by guard."""
        ticket = SupportTicket(
            opener_user_id=opener_user_id,
            subject=subject[:200] or "—",
            status="open",
            target=target if target in ("tenant_admin", "platform") else "tenant_admin",
        )
        self.session.add(ticket)
        await self.session.flush()  # get ticket.id (+ tenant_id stamped)
        self.session.add(
            TicketMessage(
                ticket_id=ticket.id, sender_kind="user",
                body=first_body, tenant_id=ticket.tenant_id,
            )
        )
        await self.session.commit()
        return ticket

    async def add_message(
        self, ticket_id: int, sender_kind: str, body: str
    ) -> tuple[SupportTicket, TicketMessage] | tuple[None, None]:
        """Append a message and move the ticket's status.

        Works under a tenant context (guard scopes the load + stamp) AND under
        the super-admin ALL_TENANTS context (tenant_id copied from the ticket).
        A load that returns nothing (wrong tenant / missing) is a safe no-op.
        """
        ticket = await self.session.get(SupportTicket, ticket_id)
        if ticket is None:
            return None, None
        msg = TicketMessage(
            ticket_id=ticket.id,
            sender_kind="admin" if sender_kind == "admin" else "user",
            body=body,
            tenant_id=ticket.tenant_id,  # explicit → valid even under ALL_TENANTS
        )
        self.session.add(msg)
        # a user reply (re)opens the thread; an admin reply marks it answered
        ticket.status = "answered" if sender_kind == "admin" else "open"
        # force the row's UPDATE even if status is unchanged, so updated_at (used
        # for inbox ordering) always reflects the latest message.
        ticket.updated_at = func.now()
        await self.session.commit()
        return ticket, msg

    async def close_ticket(self, ticket_id: int) -> bool:
        ticket = await self.session.get(SupportTicket, ticket_id)
        if ticket is None:
            return False
        ticket.status = "closed"
        await self.session.commit()
        return True

    # ---- reads -------------------------------------------------------------
    async def get(self, ticket_id: int) -> SupportTicket | None:
        return await self.session.get(SupportTicket, ticket_id)

    async def messages(self, ticket_id: int) -> list[TicketMessage]:
        rows = await self.session.scalars(
            select(TicketMessage)
            .where(TicketMessage.ticket_id == ticket_id)
            .order_by(TicketMessage.id)
        )
        return list(rows.all())

    async def list_by_target(
        self, target: str, status: str | None = None
    ) -> list[SupportTicket]:
        """Tickets of a target, newest-updated first. Scoped by the current
        context: a tenant admin sees only their tenant; the platform inbox runs
        under ALL_TENANTS and sees every tenant's platform tickets."""
        stmt = select(SupportTicket).where(SupportTicket.target == target)
        if status and status != "all":
            stmt = stmt.where(SupportTicket.status == status)
        stmt = stmt.order_by(SupportTicket.updated_at.desc(), SupportTicket.id.desc())
        rows = await self.session.scalars(stmt)
        return list(rows.all())


# --------------------------------------------------------------------------- #
#  Best-effort notifications (never raise into the caller)
# --------------------------------------------------------------------------- #
async def _build_tenant_bot(session: AsyncSession, tenant_id: int):
    """A throwaway Bot for a tenant's token (platform tenant uses the env token).
    Caller must close ``bot.session``. Returns None if no token is available."""
    try:
        from aiogram import Bot

        from app.core.config import settings
        from app.core.tenant_context import PLATFORM_TENANT_ID
        from app.models.tenant import Tenant
        from app.services.tenant_service import TenantService

        tenant = await session.scalar(select(Tenant).where(Tenant.id == tenant_id))
        token = TenantService.decrypt_token(tenant) if tenant else None
        if not token and tenant_id == PLATFORM_TENANT_ID:
            token = settings.bot_token or None
        return Bot(token=token) if token else None
    except Exception:
        return None


async def notify_opener(session: AsyncSession, ticket: SupportTicket, text: str) -> None:
    """DM the ticket opener via the ticket's tenant bot (used by panel replies)."""
    try:
        user = await session.get(User, ticket.opener_user_id)
        if user is None:
            return
        bot = await _build_tenant_bot(session, ticket.tenant_id)
        if bot is None:
            return
        try:
            await bot.send_message(user.telegram_id, text)
        finally:
            try:
                await bot.session.close()
            except Exception:
                pass
    except Exception as exc:  # pragma: no cover - best effort
        log.warning("support_notify_opener_failed", error=str(exc))


async def notify_tenant_admins(bot, session: AsyncSession, text: str) -> None:
    """DM the current tenant's admins via the given (tenant) bot instance."""
    try:
        from app.services.admin_service import AdminService

        for tg in await AdminService.admin_telegram_ids(session):
            try:
                await bot.send_message(tg, text)
            except Exception:
                pass
    except Exception as exc:  # pragma: no cover - best effort
        log.warning("support_notify_admins_failed", error=str(exc))
