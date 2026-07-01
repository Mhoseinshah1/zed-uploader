"""Provider registry — which gateways exist, which are enabled, and dispatch.

Enablement is runtime config (the ``payment_providers`` table, panel-editable):
  - centralpay: needs its env API keys (unchanged behavior) AND its row not
    switched off (a missing row counts as ON so pre-C1 deployments behave
    identically).
  - zarinpal: needs its row enabled AND a merchant id set.

``get_provider(..., for_verify=True)`` ignores the enable switch so an
in-flight payment can still be verified after an owner disables the gateway.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.payment import Payment
from app.models.payment_provider import PaymentProviderConfig

if TYPE_CHECKING:  # pragma: no cover
    from app.services.providers.base import PaymentProvider

# NOTE: provider classes and GatewayService are imported lazily inside the
# functions below. This package __init__ runs whenever any submodule (e.g.
# providers.base) is imported, and a top-level import of centralpay_service
# here would close an import cycle (centralpay_service -> gateway_service ->
# providers.base -> this __init__ -> centralpay_service).

PROVIDER_KEYS = ("centralpay", "zarinpal")


async def get_config(
    session: AsyncSession, key: str
) -> PaymentProviderConfig | None:
    return await session.scalar(
        select(PaymentProviderConfig).where(PaymentProviderConfig.key == key)
    )


async def upsert_config(
    session: AsyncSession,
    key: str,
    *,
    is_enabled: bool | None = None,
    merchant_id: str | None = None,
    sandbox: bool | None = None,
) -> PaymentProviderConfig:
    row = await get_config(session, key)
    if row is None:
        row = PaymentProviderConfig(key=key)
        session.add(row)
    if is_enabled is not None:
        row.is_enabled = is_enabled
    if merchant_id is not None:
        row.merchant_id = merchant_id.strip() or None
    if sandbox is not None:
        row.sandbox = sandbox
    await session.commit()
    return row


async def get_provider(
    session: AsyncSession, key: str, *, for_verify: bool = False
) -> PaymentProvider | None:
    """Build a configured provider, or None when unavailable/disabled."""
    from app.services.centralpay_service import CentralPayProvider
    from app.services.providers.zarinpal import ZarinpalProvider

    row = await get_config(session, key)
    if key == "centralpay":
        if not for_verify and (
            not settings.centralpay_enabled
            or (row is not None and not row.is_enabled)
        ):
            return None
        return CentralPayProvider()
    if key == "zarinpal":
        if row is None or not row.merchant_id:
            return None
        if not for_verify and not row.is_enabled:
            return None
        return ZarinpalProvider(row.merchant_id, sandbox=row.sandbox)
    return None


async def enabled_providers(session: AsyncSession) -> list[str]:
    """Provider keys currently offered to users (order = display order)."""
    out: list[str] = []
    for key in PROVIDER_KEYS:
        if await get_provider(session, key) is not None:
            out.append(key)
    return out


async def verify_order(session: AsyncSession, order_id: int) -> str:
    """Resolve an order's provider and run the idempotent verify.

    Legacy rows (pre-C1) carry provider NULL; their ``method`` holds the same
    key. Non-gateway payments (card) resolve to no provider -> "failed".
    """
    from app.services.gateway_service import GatewayService

    payment = await session.scalar(select(Payment).where(Payment.id == order_id))
    if payment is None:
        return "failed"
    key = payment.provider or payment.method
    provider = await get_provider(session, key, for_verify=True)
    if provider is None:
        return "failed"
    return await GatewayService(session, provider).verify_and_apply(order_id)
