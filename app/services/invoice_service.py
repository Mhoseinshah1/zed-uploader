"""InvoiceService (H4) — the idempotent receipt/record layer.

``record`` creates exactly one invoice per settled event (keyed by
``source_ref`` per tenant); a retry/double-callback returns the existing row
instead of inserting a duplicate. ``safe_record`` wraps it so a settlement hook
can NEVER break a payment: it runs post-commit (the money is already durable)
and swallows/rolls back any invoice error.
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.invoice import Invoice

log = get_logger("invoice")


class InvoiceService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def by_source(self, source_ref: str) -> Invoice | None:
        return await self.session.scalar(
            select(Invoice).where(Invoice.source_ref == source_ref)
        )

    async def record(
        self,
        *,
        user_id: int,
        kind: str,
        amount: int,
        method: str,
        source_ref: str,
        provider_ref: str | None = None,
    ) -> Invoice:
        """Idempotent: one invoice per (tenant, source_ref). Commits on insert.

        The reads/writes run under the current tenant context, so ``source_ref``
        uniqueness and the ``invoice_no`` sequence are per-tenant (matching the
        DB constraints). A concurrent double-insert is caught and folded into the
        already-created row.
        """
        existing = await self.by_source(source_ref)
        if existing is not None:
            return existing
        next_no = int(
            await self.session.scalar(
                select(func.coalesce(func.max(Invoice.invoice_no), 0))
            )
            or 0
        ) + 1
        invoice = Invoice(
            user_id=user_id, kind=kind, amount=int(amount), method=method,
            provider_ref=provider_ref, source_ref=source_ref, invoice_no=next_no,
        )
        self.session.add(invoice)
        try:
            await self.session.commit()
        except IntegrityError:
            await self.session.rollback()
            return await self.by_source(source_ref)
        return invoice

    async def list_for_user(self, user_id: int, limit: int = 20) -> list[Invoice]:
        rows = await self.session.scalars(
            select(Invoice)
            .where(Invoice.user_id == user_id)
            .order_by(Invoice.id.desc())
            .limit(limit)
        )
        return list(rows.all())

    async def list_for_tenant(self, limit: int = 1000) -> list[Invoice]:
        rows = await self.session.scalars(
            select(Invoice).order_by(Invoice.id.desc()).limit(limit)
        )
        return list(rows.all())

    async def get(self, invoice_id: int) -> Invoice | None:
        return await self.session.get(Invoice, invoice_id)


async def safe_record(session: AsyncSession, **kwargs) -> Invoice | None:
    """Record an invoice best-effort — never raises into a settlement path.

    Call AFTER the money has committed. On any failure it rolls back only the
    (uncommitted) invoice work and returns None; the payment/charge is untouched.
    """
    try:
        return await InvoiceService(session).record(**kwargs)
    except Exception as exc:  # pragma: no cover - defensive
        try:
            await session.rollback()
        except Exception:
            pass
        log.warning("invoice_record_failed", source=kwargs.get("source_ref"), error=str(exc))
        return None
