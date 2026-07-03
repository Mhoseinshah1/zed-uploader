"""Payment Providers — per-gateway config page (CSRF + audit).

Each provider has its own form and its own credential fields. Credentials are
write-only: the page never renders a stored value (only a "set" marker), and a
blank input keeps the current value. Nothing secret is ever logged.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_session
from app.panel.deps import audit, render, require_role, verify_csrf
from app.services.bot_setting_service import BotSettingService
from app.services.providers import (
    PROVIDER_KEYS,
    get_config,
    provider_status,
    upsert_config,
)

router = APIRouter()


def _cfg(row) -> dict:
    return dict(row.config or {}) if row is not None else {}


@router.get("/providers")
async def providers_page(
    request: Request,
    _=Depends(require_role("owner")),
    session: AsyncSession = Depends(get_session),
):
    rows = {}
    for key in PROVIDER_KEYS:
        row = await get_config(session, key)
        cfg = _cfg(row)
        rows[key] = {
            "is_enabled": row.is_enabled if row else False,
            "sandbox": row.sandbox if row else False,
            "status": await provider_status(session, key),
            # booleans only — never the values themselves
            "has": {
                "getlink_key": bool(cfg.get("getlink_key")),
                "verify_key": bool(cfg.get("verify_key")),
                "merchant_id": bool(
                    cfg.get("merchant_id") or (row.merchant_id if row else None)
                ),
                "merchant": bool(cfg.get("merchant")),
            },
        }
    return render(
        request, "providers.html",
        providers=rows,
        centralpay_env=settings.centralpay_enabled,
        stars_enabled=await BotSettingService(session).stars_enabled(),
    )


@router.post("/providers/stars")
async def providers_stars_toggle(
    request: Request,
    enabled: str = Form(""),
    csrf_token: str = Form(""),
    _=Depends(require_role("owner")),
    session: AsyncSession = Depends(get_session),
):
    """I4: global Telegram Stars on/off for this tenant (audited)."""
    await verify_csrf(request)
    from app.services.bot_setting_service import KEY_STARS_ENABLED

    on = enabled == "on"
    await BotSettingService(session).set(KEY_STARS_ENABLED, on)
    await audit(session, request, "stars_toggle", target="on" if on else "off")
    return RedirectResponse(url=f"{settings.panel_path}/providers", status_code=302)


@router.post("/providers/{key}")
async def providers_save(
    request: Request,
    key: str,
    is_enabled: str = Form(""),
    sandbox: str = Form(""),
    getlink_key: str = Form(""),
    verify_key: str = Form(""),
    merchant_id: str = Form(""),
    merchant: str = Form(""),
    csrf_token: str = Form(""),
    _=Depends(require_role("owner")),
    session: AsyncSession = Depends(get_session),
):
    await verify_csrf(request)
    if key not in PROVIDER_KEYS:
        return RedirectResponse(url=f"{settings.panel_path}/providers", status_code=302)

    config: dict = {}
    if key == "centralpay":
        config = {"getlink_key": getlink_key.strip(), "verify_key": verify_key.strip()}
    elif key == "zarinpal":
        config = {"merchant_id": merchant_id.strip()}
    elif key == "zibal":
        config = {"merchant": merchant.strip()}

    await upsert_config(
        session, key,
        is_enabled=is_enabled == "on",
        sandbox=sandbox == "on",
        config=config,  # empty strings are skipped (write-only fields)
    )
    await audit(session, request, "provider_save", target=key)  # never the values
    return RedirectResponse(url=f"{settings.panel_path}/providers", status_code=302)
