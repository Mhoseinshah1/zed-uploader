"""CallbackData factories for the admin panel.

All packed strings stay well under Telegram's 64-byte callback_data limit.
"""
from __future__ import annotations

from aiogram.filters.callback_data import CallbackData


class FilesCb(CallbackData, prefix="files"):
    """Pagination of the owner's file list."""

    page: int


class NewBotCb(CallbackData, prefix="newbot"):
    """Buy-a-bot: the customer picked a bot plan (F3)."""

    plan_key: str


class MediaCb(CallbackData, prefix="media"):
    """Per-file actions.

    action ∈ {manage, toggle_active, toggle_protect, autodel, setlimit,
    editcap, setpw, movefolder, link, stats, del, delok, back}
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


class FolderCb(CallbackData, prefix="fld"):
    """Folder browse/manage. action ∈ {root, open, new, rename, del, delok}.

    ``id`` is the folder id (0 = root, or parent for ``new``); ``page`` paginates
    the media inside a folder.
    """

    action: str
    id: int = 0
    page: int = 0


class FolderPickCb(CallbackData, prefix="fpick"):
    """Folder picker used when moving a media (id = folder id, 0 = uncategorised)."""

    id: int = 0


class SearchCb(CallbackData, prefix="srch"):
    """Search results pagination (the query itself lives in FSM state)."""

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
class AdCb(CallbackData, prefix="ad"):
    """Owner ad management. action ∈ {toggle, del, new, place}.

    For ``place`` (placement pick during creation) ``value`` carries the
    placement key.
    """

    action: str
    id: int = 0
    value: str = ""


class GateCb(CallbackData, prefix="gate"):
    """Online-gateway provider choice (when several providers are enabled).

    Exactly one of ``amount`` (wallet top-up) or ``plan`` (plan purchase) is
    set; for plans the price is re-read server-side from the plan row.
    """

    provider: str
    amount: int = 0
    plan: str = ""


class ReportCb(CallbackData, prefix="rpt"):
    """Media abuse report. action ∈ {start, reason}; value = reason key."""

    action: str
    id: int = 0
    value: str = ""


class StarsBuyCb(CallbackData, prefix="stars"):
    """Buy a plan with Telegram Stars (XTR invoice)."""

    plan: str


class PayCheckCb(CallbackData, prefix="paychk"):
    """Re-verify a CentralPay order (idempotent)."""

    order_id: int


class BuyOnlineCb(CallbackData, prefix="buyon"):
    """Buy a plan by paying online via CentralPay."""

    plan: str


class SupportCb(CallbackData, prefix="sup"):
    """In-bot support ticket actions (H2). action ∈ {reply, close}.

    ``id`` is the ticket id the opener is acting on.
    """

    action: str
    id: int


class ReactCb(CallbackData, prefix="react"):
    """J1: reaction toggle under a delivered file. kind ∈ {like, dislike, favorite}."""

    kind: str
    id: int  # media id


class CommentCb(CallbackData, prefix="cmt"):
    """J8: comments under a delivered file. action ∈ {open, write}."""

    action: str
    id: int  # media id


class BuyMediaCb(CallbackData, prefix="bym"):
    """J6: buy a paid media from the wallet (id = media id)."""

    id: int


class BrowseCb(CallbackData, prefix="brw"):
    """J1: sorted public listings. sort ∈ {popular, newest, most_viewed, favs}."""

    sort: str
    page: int = 0
