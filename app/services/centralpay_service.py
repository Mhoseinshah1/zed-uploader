"""CentralPay online gateway (redirect + pull-verify).

Money safety rests on two things (no signed IPN exists):
  1. idempotent verify keyed on our order (payment row FOR UPDATE + status check),
  2. an amount+user match check before crediting.
All credits still go through WalletService (ledger). Never re-verify or re-credit
an already-approved order; never credit on mismatch.
"""
from __future__ import annotations

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.models.payment import Payment
from app.models.user import User
from app.services.subscription_service import SubscriptionService
from app.services.wallet_service import WalletService

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


class CentralPayService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _user(self, user_id: int) -> User | None:
        return await self.session.scalar(select(User).where(User.id == user_id))

    async def start(
        self, user: User, amount: int, intent: str
    ) -> tuple[int, str] | None:
        """Create a pending payment (its id IS our orderId) and get a redirect URL.

        Returns (order_id, redirect_url) on success so the caller can offer a
        "check payment" button, or None if the gateway declined.
        """
        payment = Payment(
            user_id=user.id, amount=amount, method="centralpay",
            status="pending", intent=intent,
        )
        self.session.add(payment)
        await self.session.commit()
        await self.session.refresh(payment)

        return_url = (
            f"{settings.domain.rstrip('/')}/pay/centralpay/return?orderId={payment.id}"
        )
        resp = await post_json(
            GETLINK_URL,
            {
                "api_key": settings.centralpay_getlink_key,
                "type": "deposit",
                "amount": amount,
                "userId": user.id,
                "orderId": payment.id,
                "returnUrl": return_url,
            },
        )
        if resp.get("success"):
            log.info("centralpay_started", order_id=payment.id, amount=amount, intent=intent)
            return payment.id, resp["data"]["redirectUrl"]
        log.warning(
            "centralpay_getlink_failed",
            order_id=payment.id,
            msg=resp.get("data", {}).get("message"),
        )
        return None

    async def verify_and_apply(self, order_id: int) -> str:
        """Idempotently verify + credit. Returns credited|already|failed|mismatch."""
        payment = await self.session.scalar(
            select(Payment)
            .where(Payment.id == order_id, Payment.method == "centralpay")
            .with_for_update()
        )
        if payment is None:
            return "failed"
        if payment.status == "approved":
            return "already"  # doc rule: never re-verify a paid order
        if payment.status == "rejected":
            return "failed"

        resp = await post_json(
            VERIFY_URL,
            {"api_key": settings.centralpay_verify_key, "orderId": order_id},
        )
        if not resp.get("success"):
            return "failed"  # leave pending; the user may retry

        data = resp["data"]
        if int(data["amount"]) != int(payment.amount) or int(data["userId"]) != int(
            payment.user_id
        ):
            payment.status = "rejected"
            await self.session.commit()
            log.error("centralpay_mismatch", order_id=order_id, got=data)
            return "mismatch"  # NEVER credit on mismatch

        payment.status = "approved"
        payment.provider_ref = str(data["referenceId"])
        await WalletService(self.session).credit(
            payment.user_id,
            payment.amount,
            ttype="deposit",
            reference=f"centralpay:{data['referenceId']}",
            description="CentralPay deposit",
        )
        await self.session.commit()
        log.info("centralpay_credited", order_id=order_id, ref=data["referenceId"])

        # a "plan:<key>" intent auto-runs the purchase after a successful deposit
        if payment.intent and payment.intent.startswith("plan:"):
            user = await self._user(payment.user_id)
            if user is not None:
                await SubscriptionService(self.session).purchase(
                    user, payment.intent.split(":", 1)[1]
                )
        return "credited"
