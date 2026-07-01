"""Zarinpal gateway provider (legacy REST WebGate API, sandbox-capable).

Flow:
  create: POST PaymentRequest.json {MerchantID, Amount, Description, CallbackURL}
          -> Status 100 + Authority; redirect to /pg/StartPay/{Authority}.
  verify: POST PaymentVerification.json {MerchantID, Amount, Authority}
          -> Status 100 (or 101 = already verified) + RefID = paid.

Because verification SENDS our order amount and the gateway only confirms that
exact amount, a successful verify proves the paid amount — the VerifyResult
echoes it so the generic amount-match check holds.

``post_json`` is re-exported as a module global so tests can intercept ONLY
Zarinpal HTTP (mirroring how CentralPay tests patch centralpay_service.post_json).
"""
from __future__ import annotations

from app.core.config import settings
from app.core.logging import get_logger
from app.models.payment import Payment
from app.services.providers.base import PaymentProvider, VerifyResult
from app.services.providers.base import post_json as post_json  # noqa: PLC0414

log = get_logger("zarinpal")

_PAID_STATUSES = {100, 101}  # 100 = verified now, 101 = already verified


def _api_base(sandbox: bool) -> str:
    host = "sandbox.zarinpal.com" if sandbox else "www.zarinpal.com"
    return f"https://{host}/pg/rest/WebGate"


def _startpay_base(sandbox: bool) -> str:
    host = "sandbox.zarinpal.com" if sandbox else "www.zarinpal.com"
    return f"https://{host}/pg/StartPay"


class ZarinpalProvider(PaymentProvider):
    key = "zarinpal"
    title = "Zarinpal"

    def __init__(self, merchant_id: str, sandbox: bool = False) -> None:
        self.merchant_id = merchant_id
        self.sandbox = sandbox

    async def create(self, payment: Payment) -> str | None:
        from app.bot import messages  # Persian text stays centralized

        callback_url = (
            f"{settings.domain.rstrip('/')}/pay/zarinpal/return?orderId={payment.id}"
        )
        resp = await post_json(
            f"{_api_base(self.sandbox)}/PaymentRequest.json",
            {
                "MerchantID": self.merchant_id,
                "Amount": payment.amount,
                "Description": messages.zarinpal_description(payment.id),
                "CallbackURL": callback_url,
            },
        )
        authority = resp.get("Authority")
        if resp.get("Status") == 100 and authority:
            payment.authority = str(authority)  # persisted by the gateway service
            return f"{_startpay_base(self.sandbox)}/{authority}"
        log.warning(
            "zarinpal_request_failed", order_id=payment.id, status=resp.get("Status")
        )
        return None

    async def verify(self, payment: Payment) -> VerifyResult:
        if not payment.authority:
            return VerifyResult(ok=False)
        resp = await post_json(
            f"{_api_base(self.sandbox)}/PaymentVerification.json",
            {
                "MerchantID": self.merchant_id,
                "Amount": payment.amount,
                "Authority": payment.authority,
            },
        )
        status = resp.get("Status")
        if status in _PAID_STATUSES:
            # the gateway verified OUR amount for this authority
            return VerifyResult(
                ok=True, amount=int(payment.amount), ref=str(resp.get("RefID", ""))
            )
        log.info(
            "zarinpal_verify_not_paid", order_id=payment.id, status=status
        )
        return VerifyResult(ok=False)
