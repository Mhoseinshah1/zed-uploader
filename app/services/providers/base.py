"""Payment-provider seam.

A gateway plugs in by implementing :class:`PaymentProvider` — two async calls:
``create`` builds the gateway request for a pending Payment and returns the
redirect URL (mutating ``payment.authority`` if the gateway issues a token), and
``verify`` asks the gateway whether that Payment was actually paid.

The money-safe orchestration (FOR UPDATE + status idempotency, amount match,
WalletService-only credit, plan intent) lives in
:mod:`app.services.gateway_service` and is shared by every provider.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import httpx

from app.core.logging import get_logger
from app.models.payment import Payment

log = get_logger("providers")


async def post_json(url: str, payload: dict, timeout: float = 20.0) -> dict:
    """POST JSON and return the parsed body; never raises (returns a failure dict)."""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            return resp.json()
    except Exception as exc:  # network / non-2xx / bad JSON
        log.warning("provider_http_error", url=url, error=str(exc))
        return {}


@dataclass
class VerifyResult:
    """Outcome of a gateway verification.

    ``amount`` / ``user_id`` are echoed by the gateway when it reports them;
    the generic service refuses to credit when they do not match our order.
    """

    ok: bool
    amount: int | None = None
    ref: str | None = None
    user_id: int | None = None


class PaymentProvider(ABC):
    """One online gateway. ``key`` doubles as ``payments.method``/``provider``."""

    key: str
    title: str

    @abstractmethod
    async def create(self, payment: Payment) -> str | None:
        """Ask the gateway for a redirect URL for this pending payment.

        May set ``payment.authority`` (the caller persists it). Returns None
        when the gateway declines.
        """

    @abstractmethod
    async def verify(self, payment: Payment) -> VerifyResult:
        """Ask the gateway whether this payment was completed."""
