"""Public CentralPay return route (redirect target after the gateway).

GET /pay/centralpay/return?orderId=<int> — verifies (idempotently) and renders
a simple RTL Persian result page. Rate-limited; no API key (it's user-facing).
Requires HTTPS via nginx in production (CentralPay only returns over the domain).
"""
from __future__ import annotations

from html import escape

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from app.api.deps import DbSession, RateLimitDep
from app.core.config import settings
from app.core.logging import get_logger
from app.models.payment import Payment
from app.models.user import User
from app.services.centralpay_service import CentralPayService
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


@router.get("/pay/centralpay/return", dependencies=[RateLimitDep])
async def centralpay_return(
    request: Request, session: DbSession, orderId: int = 0
) -> HTMLResponse:
    result = await CentralPayService(session).verify_and_apply(orderId)
    log.info("centralpay_return", order_id=orderId, result=result)

    if result == "credited":
        payment = await session.scalar(select(Payment).where(Payment.id == orderId))
        bot = getattr(request.app.state, "bot", None)
        if payment is not None and bot is not None:
            user = await session.scalar(select(User).where(User.id == payment.user_id))
            balance = await WalletService(session).balance(payment.user_id)
            if user is not None:
                try:
                    from app.bot import messages

                    await bot.send_message(
                        user.telegram_id, messages.centralpay_credited(balance)
                    )
                except Exception:
                    pass

    title, body, kind = _RESULT.get(result, _RESULT["failed"])
    return HTMLResponse(_page(title, body, kind))
