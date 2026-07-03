"""Panel team — manage THIS tenant's panel users' roles (owner-only, I2).

Every action is scoped to the owner's own tenant (PanelUser is a global table,
so the tenant filter is explicit) and can only assign a per-tenant role from
PANEL_ROLES — never ``is_superadmin`` and never another tenant's login.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_session
from app.models.panel import PANEL_ROLES, PanelUser
from app.panel.deps import audit, render, require_role, verify_csrf
from app.panel.security import hash_password

router = APIRouter()


def _p(suffix: str = "") -> str:
    return f"{settings.panel_path}/team{suffix}"


async def _my_team(session: AsyncSession, tenant_id: int) -> list[PanelUser]:
    rows = await session.scalars(
        select(PanelUser)
        .where(PanelUser.tenant_id == tenant_id)
        .order_by(PanelUser.id)
    )
    return list(rows.all())


@router.get("/team")
async def team_page(
    request: Request,
    owner: PanelUser = Depends(require_role("owner")),
    session: AsyncSession = Depends(get_session),
):
    return render(
        request, "team.html",
        members=await _my_team(session, owner.tenant_id),
        roles=PANEL_ROLES, me_id=owner.id,
        error=request.query_params.get("error", ""),
    )


@router.post("/team/create")
async def team_create(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    role: str = Form("support"),
    csrf_token: str = Form(""),
    owner: PanelUser = Depends(require_role("owner")),
    session: AsyncSession = Depends(get_session),
):
    await verify_csrf(request)
    username = username.strip()
    role = role if role in PANEL_ROLES else "support"
    if not username or len(password) < 8:
        return RedirectResponse(url=_p("?error=invalid"), status_code=302)
    exists = await session.scalar(select(PanelUser).where(PanelUser.username == username))
    if exists is not None:
        return RedirectResponse(url=_p("?error=exists"), status_code=302)
    session.add(
        PanelUser(
            username=username, password_hash=hash_password(password),
            tenant_id=owner.tenant_id, role=role, is_superadmin=False, is_active=True,
        )
    )
    await session.commit()
    await audit(session, request, "panel_user_create", target=f"{username}:{role}")
    return RedirectResponse(url=_p(), status_code=302)


@router.post("/team/{user_id}/role")
async def team_set_role(
    request: Request,
    user_id: int,
    role: str = Form(...),
    csrf_token: str = Form(""),
    owner: PanelUser = Depends(require_role("owner")),
    session: AsyncSession = Depends(get_session),
):
    await verify_csrf(request)
    if role not in PANEL_ROLES:
        return RedirectResponse(url=_p("?error=invalid"), status_code=302)
    member = await session.scalar(select(PanelUser).where(PanelUser.id == user_id))
    # only within my own tenant, never a super-admin row
    if member is not None and member.tenant_id == owner.tenant_id and not member.is_superadmin:
        member.role = role
        await session.commit()
        await audit(session, request, "panel_user_role", target=f"{user_id}:{role}")
    return RedirectResponse(url=_p(), status_code=302)


async def _my_member(
    session: AsyncSession, owner: PanelUser, user_id: int
) -> PanelUser | None:
    """A member row the owner may act on: own tenant, never a super-admin."""
    member = await session.scalar(select(PanelUser).where(PanelUser.id == user_id))
    if member is None or member.tenant_id != owner.tenant_id or member.is_superadmin:
        return None
    return member


@router.post("/team/{user_id}/password")
async def team_set_password(
    request: Request,
    user_id: int,
    password: str = Form(...),
    csrf_token: str = Form(""),
    owner: PanelUser = Depends(require_role("owner")),
    session: AsyncSession = Depends(get_session),
):
    """J9: owner resets a member's password (also kills their sessions)."""
    await verify_csrf(request)
    if len(password) < 8:
        return RedirectResponse(url=_p("?error=invalid"), status_code=302)
    member = await _my_member(session, owner, user_id)
    if member is not None:
        member.password_hash = hash_password(password)
        member.session_epoch = int(member.session_epoch or 0) + 1
        await session.commit()
        await audit(session, request, "panel_user_password", target=str(user_id))
    return RedirectResponse(url=_p(), status_code=302)


@router.post("/team/{user_id}/logout_all")
async def team_logout_all(
    request: Request,
    user_id: int,
    csrf_token: str = Form(""),
    owner: PanelUser = Depends(require_role("owner")),
    session: AsyncSession = Depends(get_session),
):
    """J9: invalidate EVERY session of a member (epoch bump; self allowed)."""
    await verify_csrf(request)
    member = await _my_member(session, owner, user_id)
    if member is not None:
        member.session_epoch = int(member.session_epoch or 0) + 1
        await session.commit()
        await audit(session, request, "panel_user_logout_all", target=str(user_id))
    return RedirectResponse(url=_p(), status_code=302)


@router.post("/team/{user_id}/2fa/disable")
async def team_twofa_disable(
    request: Request,
    user_id: int,
    csrf_token: str = Form(""),
    owner: PanelUser = Depends(require_role("owner")),
    session: AsyncSession = Depends(get_session),
):
    """J9: recovery — owner turns a member's 2FA off (lost authenticator)."""
    await verify_csrf(request)
    member = await _my_member(session, owner, user_id)
    if member is not None and member.twofa_enabled:
        member.twofa_enabled = False
        member.totp_secret = None
        await session.commit()
        await audit(session, request, "panel_user_twofa_disable", target=str(user_id))
    return RedirectResponse(url=_p(), status_code=302)


@router.post("/team/{user_id}/toggle")
async def team_toggle(
    request: Request,
    user_id: int,
    csrf_token: str = Form(""),
    owner: PanelUser = Depends(require_role("owner")),
    session: AsyncSession = Depends(get_session),
):
    await verify_csrf(request)
    member = await session.scalar(select(PanelUser).where(PanelUser.id == user_id))
    # can't deactivate yourself; only within my tenant; never a super-admin
    if (
        member is not None
        and member.id != owner.id
        and member.tenant_id == owner.tenant_id
        and not member.is_superadmin
    ):
        member.is_active = not member.is_active
        await session.commit()
        await audit(
            session, request,
            "panel_user_enable" if member.is_active else "panel_user_disable",
            target=str(user_id),
        )
    return RedirectResponse(url=_p(), status_code=302)
