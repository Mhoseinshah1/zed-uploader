"""Users — list/search/detail, block/unblock, adjust balance (via WalletService)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_session
from app.models.subscription import Subscription
from app.models.user import User
from app.panel.deps import audit, render, require_role, verify_csrf
from app.services.wallet_service import InsufficientFunds, WalletService

router = APIRouter()
PAGE_SIZE = 20

# I2 role gates: viewing users is broad; wallet adjust stays finance-only.
_VIEW = ("owner", "admin", "support", "finance")
_FINANCE = ("owner", "finance")
_MODERATE = ("owner", "admin")


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
    subs = list(
        await session.scalars(
            select(Subscription)
            .where(Subscription.user_id == user_id)
            .order_by(Subscription.id.desc())
            .limit(10)
        )
    )
    txns = await WalletService(session).last_transactions(user_id, limit=10)
    return render(request, "user_detail.html", user=user, subs=subs, txns=txns, msg=msg)


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


@router.post("/users/{user_id}/adjust")
async def user_adjust(
    request: Request,
    user_id: int,
    amount: int = Form(...),
    csrf_token: str = Form(""),
    _=Depends(require_role(*_FINANCE)),
    session: AsyncSession = Depends(get_session),
):
    await verify_csrf(request)
    wallet = WalletService(session)
    msg = "adjusted"
    try:
        if amount >= 0:
            await wallet.credit(user_id, amount, ttype="adjustment", description="پنل: تعدیل")
        else:
            await wallet.debit(user_id, -amount, ttype="adjustment", description="پنل: تعدیل")
        await audit(session, request, "balance_adjust", target=f"{user_id}:{amount}")
    except InsufficientFunds:
        msg = "insufficient"
    return RedirectResponse(
        url=f"{settings.panel_path}/users/{user_id}?msg={msg}", status_code=302
    )
