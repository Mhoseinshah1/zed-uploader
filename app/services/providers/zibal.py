"""Zibal gateway provider (v1 API, sandbox-capable).

Flow:
  create: POST https://gateway.zibal.ir/v1/request {merchant, amount,
          callbackUrl, orderId, description} -> result 100 + trackId;
          redirect to https://gateway.zibal.ir/start/{trackId}.
  verify: POST https://gateway.zibal.ir/v1/verify {merchant, trackId}
          -> result 100 (or 201 = already verified) + refNumber + amount.

CURRENCY: the whole app accounts in **Toman**; the Zibal API is denominated in
**Rial**. Requests multiply by 10 on the way out and verify responses divide by
10 on the way back, so the generic amount-mismatch guard keeps comparing Toman
to Toman.

Sandbox mode uses Zibal's test merchant ("zibal") regardless of the configured
merchant id. The gateway's ``trackId`` is stored in ``payments.authority`` so
the GET return can resolve our order.

``post_json`` is re-exported as a module global so tests can intercept ONLY
Zibal HTTP (same pattern as the other providers).
"""
from __future__ import annotations

from app.core.config import settings
from app.core.logging import get_logger
from app.models.payment import Payment
from app.services.providers.base import PaymentProvider, VerifyResult
from app.services.providers.base import post_json as post_json  # noqa: PLC0414

log = get_logger("zibal")

REQUEST_URL = "https://gateway.zibal.ir/v1/request"
VERIFY_URL = "https://gateway.zibal.ir/v1/verify"
START_URL = "https://gateway.zibal.ir/start"

RIAL_PER_TOMAN = 10
_PAID_RESULTS = {100, 201}  # 100 = verified now, 201 = already verified
SANDBOX_MERCHANT = "zibal"


class ZibalProvider(PaymentProvider):
    key = "zibal"
    title = "Zibal"

    def __init__(self, merchant: str, sandbox: bool = False) -> None:
        self.merchant = SANDBOX_MERCHANT if sandbox else merchant
        self.sandbox = sandbox

    async def create(self, payment: Payment) -> str | None:
        from app.bot import messages  # Persian text stays centralized

        callback_url = (
            f"{settings.domain.rstrip('/')}/pay/zibal/return?orderId={payment.id}"
        )
        resp = await post_json(
            REQUEST_URL,
            {
                "merchant": self.merchant,
                "amount": payment.amount * RIAL_PER_TOMAN,  # Toman -> Rial
                "callbackUrl": callback_url,
                "orderId": str(payment.id),
                "description": messages.gateway_description(payment.id),
            },
        )
        track_id = resp.get("trackId")
        if resp.get("result") == 100 and track_id:
            payment.authority = str(track_id)  # persisted by the gateway service
            return f"{START_URL}/{track_id}"
        log.warning(
            "zibal_request_failed", order_id=payment.id, result=resp.get("result")
        )
        return None

    async def verify(self, payment: Payment) -> VerifyResult:
        if not payment.authority:
            return VerifyResult(ok=False)
        track: int | str = (
            int(payment.authority) if payment.authority.isdigit() else payment.authority
        )
        resp = await post_json(
            VERIFY_URL, {"merchant": self.merchant, "trackId": track}
        )
        if resp.get("result") not in _PAID_RESULTS:
            log.info(
                "zibal_verify_not_paid", order_id=payment.id, result=resp.get("result")
            )
            return VerifyResult(ok=False)
        rial = resp.get("amount")
        toman = int(rial) // RIAL_PER_TOMAN if rial is not None else None  # Rial -> Toman
        return VerifyResult(ok=True, amount=toman, ref=str(resp.get("refNumber", "")))
