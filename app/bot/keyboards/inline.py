"""Inline keyboards for the admin panel (file list, manage, settings, share)."""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot import messages
from app.bot.callbacks import (
    AdminCb,
    BatchCb,
    BcastCb,
    BuyCb,
    BuyOnlineCb,
    ChanCb,
    FilesCb,
    JoinCb,
    MediaCb,
    PayCb,
    PayCheckCb,
    SellCb,
    SetCb,
    SubCb,
    WalletCb,
)
from app.models.admin import Admin
from app.models.channel import RequiredChannel
from app.models.media import Media
from app.models.plan import Plan


def _media_type(media: Media) -> str:
    return media.files[0].file_type if media.files else messages.UNKNOWN_TYPE


def build_files_list(
    items: list[Media], page: int, total_pages: int
) -> InlineKeyboardMarkup:
    """One button per file + a prev/next navigation row when applicable."""
    builder = InlineKeyboardBuilder()
    for media in items:
        builder.row(
            InlineKeyboardButton(
                text=messages.file_row_label(media.code, _media_type(media)),
                callback_data=MediaCb(action="manage", id=media.id, page=page).pack(),
            )
        )
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(
                text=messages.LBL_PREV, callback_data=FilesCb(page=page - 1).pack()
            )
        )
    if page < total_pages - 1:
        nav.append(
            InlineKeyboardButton(
                text=messages.LBL_NEXT, callback_data=FilesCb(page=page + 1).pack()
            )
        )
    if nav:
        builder.row(*nav)
    return builder.as_markup()


def build_manage(media: Media, page: int) -> InlineKeyboardMarkup:
    """Per-file management keyboard; labels reflect current state."""
    b = InlineKeyboardBuilder()
    mid = media.id
    b.row(
        InlineKeyboardButton(
            text=messages.lbl_active(media.is_active),
            callback_data=MediaCb(action="toggle_active", id=mid, page=page).pack(),
        )
    )
    b.row(
        InlineKeyboardButton(
            text=messages.lbl_protect(media.protect_content),
            callback_data=MediaCb(action="toggle_protect", id=mid, page=page).pack(),
        )
    )
    b.row(
        InlineKeyboardButton(
            text=messages.LBL_AUTODEL,
            callback_data=MediaCb(action="autodel", id=mid, page=page).pack(),
        ),
        InlineKeyboardButton(
            text=messages.LBL_SETLIMIT,
            callback_data=MediaCb(action="setlimit", id=mid, page=page).pack(),
        ),
    )
    b.row(
        InlineKeyboardButton(
            text=messages.LBL_EDITCAP,
            callback_data=MediaCb(action="editcap", id=mid, page=page).pack(),
        )
    )
    b.row(
        InlineKeyboardButton(
            text=messages.LBL_LINK,
            callback_data=MediaCb(action="link", id=mid, page=page).pack(),
        ),
        InlineKeyboardButton(
            text=messages.LBL_STATS,
            callback_data=MediaCb(action="stats", id=mid, page=page).pack(),
        ),
    )
    b.row(
        InlineKeyboardButton(
            text=messages.LBL_DELETE,
            callback_data=MediaCb(action="del", id=mid, page=page).pack(),
        )
    )
    b.row(
        InlineKeyboardButton(
            text=messages.LBL_BACK,
            callback_data=MediaCb(action="back", id=mid, page=page).pack(),
        )
    )
    return b.as_markup()


def build_confirm_delete(media_id: int, page: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(
            text=messages.LBL_YES,
            callback_data=MediaCb(action="delok", id=media_id, page=page).pack(),
        ),
        InlineKeyboardButton(
            text=messages.LBL_NO,
            callback_data=MediaCb(action="manage", id=media_id, page=page).pack(),
        ),
    )
    return b.as_markup()


def build_settings(protect: bool, seconds: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(
            text=messages.lbl_set_protect(protect),
            callback_data=SetCb(action="protect").pack(),
        )
    )
    b.row(
        InlineKeyboardButton(
            text=messages.lbl_set_autodel(seconds),
            callback_data=SetCb(action="autodel").pack(),
        )
    )
    return b.as_markup()


def build_share(url: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text=messages.SHARE_BUTTON, url=url))
    return b.as_markup()


# --- Phase 2 keyboards -------------------------------------------------------
def _channel_url(channel: RequiredChannel) -> str | None:
    if channel.invite_link:
        return channel.invite_link
    if channel.chat_id.startswith("@"):
        return f"https://t.me/{channel.chat_id[1:]}"
    return None


def build_join_gate(channels: list[RequiredChannel], code: str) -> InlineKeyboardMarkup:
    """One URL button per joinable channel + a final recheck button."""
    b = InlineKeyboardBuilder()
    for channel in channels:
        url = _channel_url(channel)
        if url:
            b.row(
                InlineKeyboardButton(
                    text=messages.channel_join_label(channel.title, channel.chat_id),
                    url=url,
                )
            )
    b.row(
        InlineKeyboardButton(
            text=messages.GATE_RECHECK_BTN, callback_data=JoinCb(code=code).pack()
        )
    )
    return b.as_markup()


def build_channels_list(channels: list[RequiredChannel]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for channel in channels:
        b.row(
            InlineKeyboardButton(
                text=messages.channel_row_label(
                    channel.title, channel.chat_id, channel.is_active
                ),
                callback_data=ChanCb(action="toggle", id=channel.id).pack(),
            ),
            InlineKeyboardButton(
                text=messages.LBL_DELETE,
                callback_data=ChanCb(action="remove", id=channel.id).pack(),
            ),
        )
    b.row(
        InlineKeyboardButton(
            text=messages.BTN_ADD_CHANNEL,
            callback_data=ChanCb(action="add", id=0).pack(),
        )
    )
    return b.as_markup()


def build_admins_list(admins: list[Admin]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for admin in admins:
        b.row(
            InlineKeyboardButton(
                text=messages.admin_row_label(
                    admin.telegram_id, admin.role, admin.is_active
                ),
                callback_data=AdminCb(action="toggle", id=admin.id).pack(),
            ),
            InlineKeyboardButton(
                text=messages.LBL_DELETE,
                callback_data=AdminCb(action="remove", id=admin.id).pack(),
            ),
        )
    b.row(
        InlineKeyboardButton(
            text=messages.BTN_ADD_ADMIN,
            callback_data=AdminCb(action="add", id=0).pack(),
        )
    )
    return b.as_markup()


def build_batch_controls() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(
            text=messages.BTN_BATCH_FINISH,
            callback_data=BatchCb(action="finish").pack(),
        )
    )
    b.row(
        InlineKeyboardButton(
            text=messages.BTN_BATCH_CANCEL,
            callback_data=BatchCb(action="cancel").pack(),
        )
    )
    return b.as_markup()


def build_broadcast_confirm() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(
            text=messages.LBL_YES, callback_data=BcastCb(action="confirm").pack()
        ),
        InlineKeyboardButton(
            text=messages.LBL_NO, callback_data=BcastCb(action="cancel").pack()
        ),
    )
    return b.as_markup()


# --- Phase 3 keyboards -------------------------------------------------------
def build_wallet() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(
            text=messages.BTN_TOPUP, callback_data=WalletCb(action="topup").pack()
        )
    )
    b.row(
        InlineKeyboardButton(
            text=messages.BTN_TRANSACTIONS, callback_data=WalletCb(action="tx").pack()
        )
    )
    return b.as_markup()


def build_plans(plans: list[Plan]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for plan in plans:
        b.row(
            InlineKeyboardButton(
                text=messages.plan_button_label(
                    plan.title, plan.price, plan.duration_days
                ),
                callback_data=BuyCb(plan=plan.key).pack(),
            )
        )
    return b.as_markup()


def build_open_plans() -> InlineKeyboardMarkup:
    """Single 'view plans' button used by feature-gate prompts."""
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(
            text=messages.BTN_OPEN_PLANS, callback_data=SubCb(action="open").pack()
        )
    )
    return b.as_markup()


def build_payment_actions(payment_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(
            text=messages.PAY_APPROVE,
            callback_data=PayCb(action="approve", id=payment_id).pack(),
        ),
        InlineKeyboardButton(
            text=messages.PAY_REJECT,
            callback_data=PayCb(action="reject", id=payment_id).pack(),
        ),
    )
    return b.as_markup()


def build_sell(card_number: str | None, card_holder: str | None, plans: list[Plan]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(
            text=messages.BTN_SET_CARD, callback_data=SellCb(action="card").pack()
        ),
        InlineKeyboardButton(
            text=messages.BTN_SET_HOLDER, callback_data=SellCb(action="holder").pack()
        ),
    )
    for plan in plans:
        if plan.key == "free":
            continue
        b.row(
            InlineKeyboardButton(
                text=messages.sell_price_label(plan.title, plan.price),
                callback_data=SellCb(action="price", key=plan.key).pack(),
            ),
            InlineKeyboardButton(
                text=messages.sell_duration_label(plan.title, plan.duration_days),
                callback_data=SellCb(action="duration", key=plan.key).pack(),
            ),
        )
    return b.as_markup()


def build_buy_confirm(plan_key: str, centralpay: bool = False) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(
            text=messages.BTN_PAY_WALLET,
            callback_data=BuyCb(plan=plan_key, ok=1).pack(),
        )
    )
    if centralpay:
        b.row(
            InlineKeyboardButton(
                text=messages.BTN_PAY_ONLINE,
                callback_data=BuyOnlineCb(plan=plan_key).pack(),
            )
        )
    return b.as_markup()


def build_topup_methods(centralpay: bool) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(
            text=messages.BTN_PAY_CARD, callback_data=WalletCb(action="card").pack()
        )
    )
    if centralpay:
        b.row(
            InlineKeyboardButton(
                text=messages.BTN_PAY_ONLINE,
                callback_data=WalletCb(action="online").pack(),
            )
        )
    return b.as_markup()


def build_centralpay(redirect_url: str, order_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text=messages.BTN_PAY_LINK, url=redirect_url))
    b.row(
        InlineKeyboardButton(
            text=messages.BTN_PAY_CHECK,
            callback_data=PayCheckCb(order_id=order_id).pack(),
        )
    )
    return b.as_markup()
