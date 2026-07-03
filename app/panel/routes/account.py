"""My account (J9): change own password + optional TOTP 2FA (off by default).

The TOTP secret is generated server-side, stored Fernet-ENCRYPTED and shown
exactly once — on the setup screen right after generation (as base32 + an
``otpauth://`` URI to paste into any authenticator app; no external QR
service, the panel CSP forbids it). It is never rendered or logged again.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.crypto import decrypt_secret, encrypt_secret
from app.db.session import get_session
from app.models.panel import PanelUser
from app.panel.deps import audit, render, require_panel_user, verify_csrf
from app.panel.security import hash_password, verify_password
from app.panel.totp import generate_secret, provisioning_uri, verify_totp

router = APIRouter()


def _p(suffix: str = "") -> str:
    return f"{settings.panel_path}/account{suffix}"


def _page(request: Request, user: PanelUser, **extra):
    context = {
        "twofa_enabled": bool(user.twofa_enabled),
        "twofa_pending": bool(user.totp_secret) and not user.twofa_enabled,
        "error": request.query_params.get("error", ""),
        "ok": request.query_params.get("ok", ""),
        "new_secret": None,
        "otpauth": None,
    }
    context.update(extra)
    return render(request, "account.html", **context)


@router.get("/account")
async def account_page(
    request: Request,
    user: PanelUser = Depends(require_panel_user),
):
    return _page(request, user)


@router.post("/account/password")
async def account_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    csrf_token: str = Form(""),
    user: PanelUser = Depends(require_panel_user),
    session: AsyncSession = Depends(get_session),
):
    await verify_csrf(request)
    if not verify_password(current_password, user.password_hash):
        return RedirectResponse(url=_p("?error=badpass"), status_code=302)
    if len(new_password) < 8:
        return RedirectResponse(url=_p("?error=short"), status_code=302)
    user.password_hash = hash_password(new_password)
    # bump the epoch: every outstanding session (this one included) dies
    user.session_epoch = int(user.session_epoch or 0) + 1
    await session.commit()
    await audit(session, request, "panel_password_change", target=user.username)
    return RedirectResponse(url=f"{settings.panel_path}/login", status_code=302)


@router.post("/account/2fa/start")
async def twofa_start(
    request: Request,
    csrf_token: str = Form(""),
    user: PanelUser = Depends(require_panel_user),
    session: AsyncSession = Depends(get_session),
):
    await verify_csrf(request)
    if user.twofa_enabled:
        return RedirectResponse(url=_p(), status_code=302)
    secret = generate_secret()
    user.totp_secret = encrypt_secret(secret)  # ciphertext at rest
    await session.commit()
    # the ONLY response that ever carries the plaintext secret
    return _page(
        request, user,
        new_secret=secret, otpauth=provisioning_uri(secret, user.username),
        twofa_pending=True,
    )


@router.post("/account/2fa/enable")
async def twofa_enable(
    request: Request,
    code: str = Form(...),
    csrf_token: str = Form(""),
    user: PanelUser = Depends(require_panel_user),
    session: AsyncSession = Depends(get_session),
):
    await verify_csrf(request)
    if user.twofa_enabled or not user.totp_secret:
        return RedirectResponse(url=_p(), status_code=302)
    try:
        secret = decrypt_secret(user.totp_secret)
    except Exception:
        return RedirectResponse(url=_p("?error=code"), status_code=302)
    if not verify_totp(secret, code):
        return RedirectResponse(url=_p("?error=code"), status_code=302)
    user.twofa_enabled = True
    await session.commit()
    await audit(session, request, "twofa_enable", target=user.username)
    return RedirectResponse(url=_p("?ok=enabled"), status_code=302)


@router.post("/account/2fa/disable")
async def twofa_disable(
    request: Request,
    current_password: str = Form(...),
    csrf_token: str = Form(""),
    user: PanelUser = Depends(require_panel_user),
    session: AsyncSession = Depends(get_session),
):
    await verify_csrf(request)
    if not verify_password(current_password, user.password_hash):
        return RedirectResponse(url=_p("?error=badpass"), status_code=302)
    user.twofa_enabled = False
    user.totp_secret = None
    await session.commit()
    await audit(session, request, "twofa_disable", target=user.username)
    return RedirectResponse(url=_p("?ok=disabled"), status_code=302)
