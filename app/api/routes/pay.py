"""Public gateway return route (redirect target after a payment).

GET /pay/{provider}/return?orderId=<int> — verifies (idempotently) and renders
a simple RTL Persian result page. Rate-limited; no API key (it's user-facing).
The same route shape serves every provider: CentralPay returns with our
``orderId``; Zarinpal appends ``Authority`` + ``Status`` (OK|NOK) to the
callback, and the payment can also be resolved by its stored authority.
"""
from __future__ import annotations

from html import escape

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from app.api.deps import DbSession, RateLimitDep
from app.core.config import settings
from app.core.logging import get_logger
from app.models.payment import Payment
from app.models.user import User
from app.services.providers import PROVIDER_KEYS, verify_order
from app.services.wallet_service import WalletService

router = APIRouter(tags=["pay"])
log = get_logger("pay")

_RESULT = {
    "credited": ("پرداخت موفق", "پرداخت با موفقیت انجام شد و حساب شما شارژ شد.", "ok"),
    "already": ("قبلاً تأیید شده", "این پرداخت پیش‌تر تأیید و اعمال شده است.", "ok"),
    "failed": ("پرداخت ناموفق", "پرداختی یافت نشد یا هنوز کامل نشده است. اگر پرداخت کرده‌اید، کمی بعد دوباره تلاش کنید.", "err"),
    "mismatch": ("مغایرت مبلغ", "مبلغ پرداخت با سفارش هم‌خوانی ندارد. لطفاً با پشتیبانی تماس بگیرید.", "err"),
}


def _page(title: str, body: str, kind: str) -> str:
    color = "#22c55e" if kind == "ok" else "#ef4444"
    bot_url = f"https://t.me/{escape(settings.bot_username)}"
    return f"""<!DOCTYPE html>
<html lang="fa" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title>
<style>
body{{font-family:Tahoma,sans-serif;background:#0f1115;color:#e6e9ef;display:flex;
min-height:100vh;align-items:center;justify-content:center;margin:0}}
.card{{background:#171a21;border:1px solid #262b36;border-radius:12px;padding:28px;
max-width:360px;text-align:center}}
h1{{color:{color};font-size:1.3rem;margin:0 0 12px}}
p{{color:#c7ccd8;line-height:1.9}}
a.btn{{display:inline-block;margin-top:16px;background:#3b82f6;color:#fff;
padding:10px 18px;border-radius:8px;text-decoration:none}}
</style></head><body>
<div class="card"><h1>{escape(title)}</h1><p>{escape(body)}</p>
<a class="btn" href="{bot_url}">بازگشت به ربات</a></div>
</body></html>"""


def _render(result: str) -> HTMLResponse:
    title, body, kind = _RESULT.get(result, _RESULT["failed"])
    return HTMLResponse(_page(title, body, kind))


async def _resolve_payment(
    session, provider: str, order_id: int, token: str
) -> Payment | None:
    if order_id > 0:
        return await session.scalar(
            select(Payment).where(
                Payment.id == order_id, Payment.method == provider
            )
        )
    if token:
        return await session.scalar(
            select(Payment).where(
                Payment.authority == token, Payment.method == provider
            )
        )
    return None


@router.get("/pay/{provider}/return", dependencies=[RateLimitDep])
async def gateway_return(
    provider: str,
    request: Request,
    session: DbSession,
    orderId: int = 0,
    authority: str = Query("", alias="Authority"),
    status: str = Query("", alias="Status"),
    track_id: str = Query("", alias="trackId"),
    success: str = Query("", alias="success"),
) -> HTMLResponse:
    if provider not in PROVIDER_KEYS:
        return _render("failed")

    from app.core.tenant_context import all_tenants, reset_tenant, set_tenant

    # The callback arrives with NO tenant context. Resolve the payment across
    # ALL tenants (it is tenant-scoped, but we don't yet know which), then run
    # everything under THAT payment's tenant so verify/credit is scoped to it.
    token = authority or track_id  # Zarinpal Authority / Zibal trackId
    with all_tenants():
        payment = await _resolve_payment(session, provider, orderId, token)
    if payment is None:
        log.info("gateway_return_unknown", provider=provider, order_id=orderId)
        return _render("failed")  # fail closed — never guess a tenant

    ctx = set_tenant(payment.tenant_id)
    try:
        # a mismatched gateway token must never verify someone else's order
        if token and payment.authority and token != payment.authority:
            log.warning("gateway_return_token_mismatch", order_id=payment.id)
            return _render("failed")
        if (status and status.upper() != "OK") or success == "0":
            log.info("gateway_return_not_ok", provider=provider, order_id=payment.id)
            return _render("failed")

        # verify_order keeps all A1 guarantees (idempotent, amount-match) and now
        # credits the payment's own user within the payment's tenant.
        result = await verify_order(session, payment.id)
        log.info(
            "gateway_return", provider=provider, order_id=payment.id,
            tenant_id=payment.tenant_id, result=result,
        )

        if result == "credited":
            user = await session.scalar(select(User).where(User.id == payment.user_id))
            balance = await WalletService(session).balance(payment.user_id)
            bot = await _tenant_notify_bot(request, session, payment.tenant_id)
            if user is not None and bot is not None:
                try:
                    from app.bot import messages

                    await bot.send_message(
                        user.telegram_id, messages.centralpay_credited(balance)
                    )
                except Exception:
                    pass
        return _render(result)
    finally:
        reset_tenant(ctx)


async def _tenant_notify_bot(request: Request, session, tenant_id: int):
    """The bot to notify the buyer with: the platform bot for tenant 1, else the
    tenant's registered bot (API process holds the registry). None if absent —
    the credit already happened; the notice is best-effort."""
    from app.core.tenant_context import PLATFORM_TENANT_ID

    if tenant_id == PLATFORM_TENANT_ID:
        return getattr(request.app.state, "bot", None)
    registry = getattr(request.app.state, "registry", None)
    if registry is None:
        return None
    from app.models.tenant import Tenant

    tenant = await session.scalar(select(Tenant).where(Tenant.id == tenant_id))
    entry = registry.get(tenant.bot_id) if tenant and tenant.bot_id else None
    return entry.bot if entry else None
