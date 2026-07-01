"""CentralPay online gateway (redirect + pull-verify).

Since C1 this is a :class:`PaymentProvider` implementation; the money-safety
core (idempotent FOR-UPDATE verify, amount+user match, WalletService-only
credit, plan intent) moved unchanged into the shared
:class:`app.services.gateway_service.GatewayService`. The public
``CentralPayService`` API (start / verify_and_apply) is preserved verbatim.

``post_json`` stays a module global here so existing tests (and operators)
can intercept CentralPay HTTP without touching other providers.
"""
from __future__ import annotations

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.models.payment import Payment
from app.models.user import User
from app.services.gateway_service import GatewayService
from app.services.providers.base import PaymentProvider, VerifyResult

log = get_logger("centralpay")

GETLINK_URL = "https://centralapi.org/webservice/basic/getLink.php"
VERIFY_URL = "https://centralapi.org/webservice/basic/verify.php"


async def post_json(url: str, payload: dict, timeout: float = 20.0) -> dict:
    """POST JSON and return the parsed body; never raises (returns a failure dict)."""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            return resp.json()
    except Exception as exc:  # network / non-2xx / bad JSON
        log.warning("centralpay_http_error", url=url, error=str(exc))
        return {"success": False, "data": {"message": "http_error"}}


class CentralPayProvider(PaymentProvider):
    key = "centralpay"
    title = "CentralPay"

    async def create(self, payment: Payment) -> str | None:
        return_url = (
            f"{settings.domain.rstrip('/')}/pay/centralpay/return?orderId={payment.id}"
        )
        resp = await post_json(
            GETLINK_URL,
            {
                "api_key": settings.centralpay_getlink_key,
                "type": "deposit",
                "amount": payment.amount,
                "userId": payment.user_id,
                "orderId": payment.id,
                "returnUrl": return_url,
            },
        )
        if resp.get("success"):
            return resp["data"]["redirectUrl"]
        log.warning(
            "centralpay_getlink_failed",
            order_id=payment.id,
            msg=resp.get("data", {}).get("message"),
        )
        return None

    async def verify(self, payment: Payment) -> VerifyResult:
        resp = await post_json(
            VERIFY_URL,
            {"api_key": settings.centralpay_verify_key, "orderId": payment.id},
        )
        if not resp.get("success"):
            return VerifyResult(ok=False)
        data = resp["data"]
        return VerifyResult(
            ok=True,
            amount=int(data["amount"]),
            ref=str(data["referenceId"]),
            user_id=int(data["userId"]),
        )


class CentralPayService:
    """Backward-compatible facade over the generic gateway service."""

    def __init__(self, session: AsyncSession) -> None:
        self._gateway = GatewayService(session, CentralPayProvider())

    async def start(
        self, user: User, amount: int, intent: str
    ) -> tuple[int, str] | None:
        return await self._gateway.start(user, amount, intent)

    async def verify_and_apply(self, order_id: int) -> str:
        return await self._gateway.verify_and_apply(order_id)
