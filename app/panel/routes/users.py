"""Users — list/search/detail, block, manual wallet + subscription management.

All balance changes go through WalletService (ledger row each). Manual wallet
ops carry reason + a ``panel:<panel_user_id>`` reference, are audited, and
best-effort-notify the user. Subscription grants are audited too (no ledger).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import messages
from app.core.config import settings
from app.db.session import get_session
from app.models.panel import PanelUser
from app.models.plan import Plan
from app.models.subscription import Subscription
from app.models.user import User
from app.panel.deps import audit, render, require_role, verify_csrf
from app.services.notify import notify_user
from app.services.wallet_service import InsufficientFunds, WalletService

router = APIRouter()
PAGE_SIZE = 20
LARGE_ADJUST = 5_000_000  # tomans; above this the wallet op needs an explicit confirm

# I2 role gates: viewing users is broad; wallet adjust stays finance-only.
_VIEW = ("owner", "admin", "support", "finance")
_FINANCE = ("owner", "finance")
_MODERATE = ("owner", "admin")
_SUBS = ("owner", "admin")


async def _detail_context(session: AsyncSession, user: User) -> dict:
    subs = list(
        await session.scalars(
            select(Subscription)
            .where(Subscription.user_id == user.id)
            .order_by(Subscription.id.desc())
            .limit(10)
        )
    )
    txns = await WalletService(session).last_transactions(user.id, limit=10)
    plans = list(await session.scalars(select(Plan).order_by(Plan.id)))
    return {"user": user, "subs": subs, "txns": txns, "plans": plans}


@router.get("/users")
async def users_list(
    request: Request,
    q: str = "",
    page: int = 0,
    _=Depends(require_role(*_VIEW)),
    session: AsyncSession = Depends(get_session),
):
    stmt = select(User)
    q = q.strip()
    if q:
        conditions = [User.username.ilike(f"%{q}%")]
        if q.isdigit():
            conditions.append(User.telegram_id == int(q))
        stmt = stmt.where(or_(*conditions))
    total = int(await session.scalar(select(func.count()).select_from(stmt.subquery())))
    page = max(0, page)
    rows = list(
        await session.scalars(
            stmt.order_by(User.id.desc()).limit(PAGE_SIZE).offset(page * PAGE_SIZE)
        )
    )
    return render(
        request, "users.html", users=rows, q=q, page=page, total=total,
        page_size=PAGE_SIZE,
    )


@router.get("/users/{user_id}")
async def user_detail(
    request: Request,
    user_id: int,
    msg: str = "",
    _=Depends(require_role(*_VIEW)),
    session: AsyncSession = Depends(get_session),
):
    user = await session.scalar(select(User).where(User.id == user_id))
    if user is None:
        return RedirectResponse(url=f"{settings.panel_path}/users", status_code=302)
    ctx = await _detail_context(session, user)
    return render(request, "user_detail.html", msg=msg, pending=None, **ctx)


@router.post("/users/{user_id}/block")
async def user_block(
    request: Request,
    user_id: int,
    csrf_token: str = Form(""),
    _=Depends(require_role(*_MODERATE)),
    session: AsyncSession = Depends(get_session),
):
    await verify_csrf(request)
    user = await session.scalar(select(User).where(User.id == user_id))
    if user is not None:
        user.is_blocked = not user.is_blocked
        await session.commit()
        await audit(
            session, request,
            "user_block" if user.is_blocked else "user_unblock",
            target=str(user_id),
        )
    return RedirectResponse(url=f"{settings.panel_path}/users/{user_id}", status_code=302)


@router.post("/users/{user_id}/wallet")
async def user_wallet(
    request: Request,
    user_id: int,
    direction: str = Form(...),  # credit (شارژ) | debit (کسر)
    amount: int = Form(...),
    reason: str = Form(""),
    confirm: str = Form(""),
    csrf_token: str = Form(""),
    panel_user: PanelUser = Depends(require_role(*_FINANCE)),
    session: AsyncSession = Depends(get_session),
):
    await verify_csrf(request)
    reason = reason.strip()
    user = await session.scalar(select(User).where(User.id == user_id))
    if user is None:
        return RedirectResponse(url=f"{settings.panel_path}/users", status_code=302)
    if direction not in ("credit", "debit") or amount <= 0 or not reason:
        return _redirect(user_id, "invalid")

    # large amounts require an explicit confirmation step
    if amount >= LARGE_ADJUST and confirm != "1":
        ctx = await _detail_context(session, user)
        return render(
            request, "user_detail.html", msg="", pending={
                "direction": direction, "amount": amount, "reason": reason,
            }, **ctx,
        )

    ref = f"panel:{panel_user.id}"
    label = "شارژ دستی" if direction == "credit" else "کسر دستی"
    description = f"پنل: {label} — {reason}"
    try:
        wallet = WalletService(session)
        if direction == "credit":
            await wallet.credit(user_id, amount, ttype="adjustment", reference=ref, description=description)
            notice = messages.wallet_credited_notice(amount, reason)
        else:
            await wallet.debit(user_id, amount, ttype="adjustment", reference=ref, description=description)
            notice = messages.wallet_debited_notice(amount, reason)
        await audit(session, request, f"wallet_{direction}", target=f"{user_id}:{amount}:{reason}")
        await notify_user(session, user_id, notice)  # best-effort
        return _redirect(user_id, "adjusted")
    except InsufficientFunds:
        return _redirect(user_id, "insufficient")


@router.post("/users/{user_id}/subscription")
async def user_subscription(
    request: Request,
    user_id: int,
    action: str = Form(...),  # change | extend | expiry | lifetime | cancel
    plan: str = Form(""),
    days: int = Form(0),
    date: str = Form(""),
    csrf_token: str = Form(""),
    _=Depends(require_role(*_SUBS)),
    session: AsyncSession = Depends(get_session),
):
    await verify_csrf(request)
    user = await session.scalar(select(User).where(User.id == user_id))
    if user is None:
        return RedirectResponse(url=f"{settings.panel_path}/users", status_code=302)
    now = datetime.now(timezone.utc)

    def _cur_exp() -> datetime | None:
        exp = user.plan_expires_at
        if exp is not None and exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        return exp

    if action == "cancel":
        user.plan = "free"
        user.plan_expires_at = None
        await session.execute(
            Subscription.__table__.update()
            .where(Subscription.user_id == user_id, Subscription.is_active.is_(True))
            .values(is_active=False)
        )
        await session.commit()
        await audit(session, request, "subscription_cancel", target=str(user_id))
        await notify_user(session, user_id, messages.SUBSCRIPTION_CANCELLED_NOTICE)
        return _redirect(user_id, "sub_updated")

    if action == "change":
        if plan:
            user.plan = plan
    elif action == "extend":
        base = _cur_exp() if (_cur_exp() and _cur_exp() > now) else now
        user.plan_expires_at = base + timedelta(days=max(1, days))
    elif action == "expiry":
        try:
            user.plan_expires_at = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            return _redirect(user_id, "invalid")
    elif action == "lifetime":
        user.plan_expires_at = None
    else:
        return _redirect(user_id, "invalid")

    # record the grant as a Subscription row (audited; no financial ledger)
    session.add(
        Subscription(
            user_id=user_id, plan=user.plan, starts_at=now,
            expires_at=user.plan_expires_at, is_active=True,
        )
    )
    await session.commit()
    await audit(session, request, f"subscription_{action}", target=f"{user_id}:{user.plan}")
    exp_str = user.plan_expires_at.strftime("%Y-%m-%d") if user.plan_expires_at else None
    await notify_user(session, user_id, messages.subscription_changed_notice(user.plan, exp_str))
    return _redirect(user_id, "sub_updated")


def _redirect(user_id: int, msg: str) -> RedirectResponse:
    return RedirectResponse(
        url=f"{settings.panel_path}/users/{user_id}?msg={msg}", status_code=302
    )
