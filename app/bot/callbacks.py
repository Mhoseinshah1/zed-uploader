"""CallbackData factories for the admin panel.

All packed strings stay well under Telegram's 64-byte callback_data limit.
"""
from __future__ import annotations

from aiogram.filters.callback_data import CallbackData


class FilesCb(CallbackData, prefix="files"):
    """Pagination of the owner's file list."""

    page: int


class MediaCb(CallbackData, prefix="media"):
    """Per-file actions.

    action ∈ {manage, toggle_active, toggle_protect, autodel, setlimit,
    editcap, setpw, link, stats, del, delok, back}
    """

    action: str
    id: int
    page: int = 0


class SetCb(CallbackData, prefix="set"):
    """Settings actions. action ∈ {protect, autodel}."""

    action: str


# --- Phase 2 ---------------------------------------------------------------
class JoinCb(CallbackData, prefix="join"):
    """Force-join recheck for a media code."""

    code: str


class ChanCb(CallbackData, prefix="chan"):
    """Channel management. action ∈ {toggle, remove}."""

    action: str
    id: int


class AdminCb(CallbackData, prefix="adm"):
    """Admin management. action ∈ {toggle, remove}."""

    action: str
    id: int


class BatchCb(CallbackData, prefix="batch"):
    """Batch upload. action ∈ {finish, cancel}."""

    action: str


class BcastCb(CallbackData, prefix="bcast"):
    """Broadcast confirm. action ∈ {confirm, cancel}."""

    action: str


class ReviewCb(CallbackData, prefix="rev"):
    """Upload review queue. action ∈ {list, view, approve, reject}."""

    action: str
    id: int = 0
    page: int = 0


# --- Phase 3 (monetization) ------------------------------------------------
class WalletCb(CallbackData, prefix="wal"):
    """Wallet actions. action ∈ {topup, tx}."""

    action: str


class SubCb(CallbackData, prefix="sub"):
    """Subscription menu. action ∈ {open}."""

    action: str


class BuyCb(CallbackData, prefix="buy"):
    """Buy a plan by key. ok=0 shows the confirm, ok=1 performs the purchase."""

    plan: str
    ok: int = 0


class PayCb(CallbackData, prefix="pay"):
    """Owner payment decision. action ∈ {approve, reject}."""

    action: str
    id: int


class SellCb(CallbackData, prefix="sell"):
    """Owner sell settings. action ∈ {card, holder, price, duration}.

    ``key`` is the plan key for price/duration; None for card/holder. It must be
    Optional because aiogram unpacks an empty segment back to None.
    """

    action: str
    key: str | None = None


# --- Phase 5 (CentralPay) --------------------------------------------------
class PayCheckCb(CallbackData, prefix="paychk"):
    """Re-verify a CentralPay order (idempotent)."""

    order_id: int


class BuyOnlineCb(CallbackData, prefix="buyon"):
    """Buy a plan by paying online via CentralPay."""

    plan: str
