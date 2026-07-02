"""Dashboard — read-only stat cards + highlights (reuses service counts)."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.models.media import Media
from app.models.payment import Payment
from app.models.subscription import Subscription
from app.panel.deps import render, require_panel_user
from app.services.media_service import MediaService
from app.services.user_service import UserService

router = APIRouter()


@router.get("")
async def dashboard(
    request: Request,
    _=Depends(require_panel_user),
    session: AsyncSession = Depends(get_session),
):
    media_service = MediaService(session)
    month_start = datetime.now(timezone.utc).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )

    revenue = int(
        await session.scalar(
            select(func.coalesce(func.sum(Payment.amount), 0)).where(
                Payment.status == "approved", Payment.created_at >= month_start
            )
        )
        or 0
    )
    pending = int(
        await session.scalar(
            select(func.count(Payment.id)).where(Payment.status == "pending")
        )
        or 0
    )
    active_subs = int(
        await session.scalar(
            select(func.count(Subscription.id)).where(Subscription.is_active.is_(True))
        )
        or 0
    )
    top_files = list(
        await session.scalars(
            select(Media).order_by(Media.download_count.desc()).limit(5)
        )
    )
    recent_payments = list(
        await session.scalars(select(Payment).order_by(Payment.id.desc()).limit(5))
    )

    from app.core.version import code_version, installed_version

    stats = {
        "total_users": await UserService(session).count_users(),
        "total_media": await media_service.count_media(),
        "total_downloads": await media_service.total_downloads(),
        "revenue": revenue,
        "pending": pending,
        "active_subs": active_subs,
    }
    return render(
        request,
        "dashboard.html",
        stats=stats,
        top_files=top_files,
        recent_payments=recent_payments,
        installed_version=await installed_version(session),
        code_version=code_version(),
    )
