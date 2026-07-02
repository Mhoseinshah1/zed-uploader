"""Provider registry — which gateways exist, which are READY, and dispatch.

A provider is offered to users only when its row is enabled AND it is fully
configured (its required credentials are present):
  - centralpay: getlink/verify keys from its ``config`` row, falling back to
    the CENTRALPAY_* env vars — so pre-C1b deployments keep working unchanged.
    A missing row counts as enabled (0010 seeded it on).
  - zarinpal: merchant_id from ``config`` (falling back to the legacy
    merchant_id column) + the sandbox flag.
  - zibal: merchant from ``config``; sandbox mode uses Zibal's public test
    merchant, so sandbox counts as configured.

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

PROVIDER_KEYS = ("centralpay", "zarinpal", "zibal")

# panel status badge values
STATUS_DISABLED = "disabled"
STATUS_UNCONFIGURED = "enabled-but-unconfigured"
STATUS_READY = "ready"


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
    config: dict | None = None,
) -> PaymentProviderConfig:
    """Update ONE provider's row; only the given fields change.

    ``config`` entries are merged key-by-key; empty-string values are skipped
    (the panel's write-only masked inputs post "" for "keep the current value").
    """
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
    if config:
        merged = dict(row.config or {})
        for cfg_key, value in config.items():
            if value is None or value == "":
                continue  # write-only field left blank -> keep existing
            merged[cfg_key] = value
        row.config = merged
    await session.commit()
    return row


def _cfg(row: PaymentProviderConfig | None) -> dict:
    return dict(row.config or {}) if row is not None else {}


def _centralpay_keys(row: PaymentProviderConfig | None) -> tuple[str, str]:
    cfg = _cfg(row)
    return (
        cfg.get("getlink_key") or settings.centralpay_getlink_key,
        cfg.get("verify_key") or settings.centralpay_verify_key,
    )


def _zarinpal_merchant(row: PaymentProviderConfig | None) -> str | None:
    cfg = _cfg(row)
    return cfg.get("merchant_id") or (row.merchant_id if row is not None else None)


def _zibal_merchant(row: PaymentProviderConfig | None) -> str | None:
    if row is not None and row.sandbox:
        from app.services.providers.zibal import SANDBOX_MERCHANT

        return SANDBOX_MERCHANT
    return _cfg(row).get("merchant") or None


def _is_configured(key: str, row: PaymentProviderConfig | None) -> bool:
    if key == "centralpay":
        getlink, verify = _centralpay_keys(row)
        return bool(getlink and verify)
    if key == "zarinpal":
        return bool(_zarinpal_merchant(row))
    if key == "zibal":
        return bool(_zibal_merchant(row))
    return False


def _is_enabled(key: str, row: PaymentProviderConfig | None) -> bool:
    if row is None:
        # 0010 seeded centralpay ON; a pre-seed DB must behave the same
        return key == "centralpay"
    return row.is_enabled


async def provider_status(session: AsyncSession, key: str) -> str:
    """Panel badge: disabled / enabled-but-unconfigured / ready."""
    row = await get_config(session, key)
    if not _is_enabled(key, row):
        return STATUS_DISABLED
    return STATUS_READY if _is_configured(key, row) else STATUS_UNCONFIGURED


async def get_provider(
    session: AsyncSession, key: str, *, for_verify: bool = False
) -> PaymentProvider | None:
    """Build a configured provider, or None when unavailable/not ready."""
    from app.services.centralpay_service import CentralPayProvider
    from app.services.providers.zarinpal import ZarinpalProvider
    from app.services.providers.zibal import ZibalProvider

    row = await get_config(session, key)
    if key == "centralpay":
        if not for_verify and not (
            _is_enabled(key, row) and _is_configured(key, row)
        ):
            return None
        getlink, verify = _centralpay_keys(row)
        return CentralPayProvider(getlink or None, verify or None)
    if key == "zarinpal":
        merchant = _zarinpal_merchant(row)
        if not merchant:
            return None  # cannot even verify without a merchant
        if not for_verify and not _is_enabled(key, row):
            return None
        return ZarinpalProvider(merchant, sandbox=bool(row and row.sandbox))
    if key == "zibal":
        merchant = _zibal_merchant(row)
        if not merchant:
            return None
        if not for_verify and not _is_enabled(key, row):
            return None
        return ZibalProvider(merchant, sandbox=bool(row and row.sandbox))
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
