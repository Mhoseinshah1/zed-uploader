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
    editcap, link, stats, del, delok, back}
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
