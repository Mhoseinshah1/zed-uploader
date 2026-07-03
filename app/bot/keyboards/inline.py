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
    FolderCb,
    FolderPickCb,
    GateCb,
    JoinCb,
    MediaCb,
    PayCb,
    PayCheckCb,
    ReportCb,
    ReviewCb,
    SearchCb,
    SellCb,
    SetCb,
    StarsBuyCb,
    SubCb,
    WalletCb,
)
from app.models.admin import Admin
from app.models.channel import RequiredChannel
from app.models.folder import Folder
from app.models.media import Media
from app.models.plan import Plan


def _media_type(media: Media) -> str:
    return media.files[0].file_type if media.files else messages.UNKNOWN_TYPE


def build_folders_root(folders: list[Folder]) -> InlineKeyboardMarkup:
    """Root folder listing: one button per folder + a 'new folder' button."""
    b = InlineKeyboardBuilder()
    for folder in folders:
        b.row(
            InlineKeyboardButton(
                text=f"📁 {folder.name}",
                callback_data=FolderCb(action="open", id=folder.id).pack(),
            )
        )
    b.row(
        InlineKeyboardButton(
            text=messages.LBL_NEW_FOLDER,
            callback_data=FolderCb(action="new", id=0).pack(),
        )
    )
    return b.as_markup()


def build_folder_view(
    folder: Folder,
    subfolders: list[Folder],
    media_items: list[Media],
    page: int,
    total_pages: int,
) -> InlineKeyboardMarkup:
    """A folder's subfolders + its media (as manage links) + action buttons."""
    b = InlineKeyboardBuilder()
    for sub in subfolders:
        b.row(
            InlineKeyboardButton(
                text=f"📁 {sub.name}",
                callback_data=FolderCb(action="open", id=sub.id).pack(),
            )
        )
    for media in media_items:
        b.row(
            InlineKeyboardButton(
                text=messages.file_row_label(media.code, _media_type(media)),
                callback_data=MediaCb(action="manage", id=media.id, page=0).pack(),
            )
        )
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(
                text=messages.LBL_PREV,
                callback_data=FolderCb(action="open", id=folder.id, page=page - 1).pack(),
            )
        )
    if page < total_pages - 1:
        nav.append(
            InlineKeyboardButton(
                text=messages.LBL_NEXT,
                callback_data=FolderCb(action="open", id=folder.id, page=page + 1).pack(),
            )
        )
    if nav:
        b.row(*nav)
    b.row(
        InlineKeyboardButton(
            text=messages.LBL_NEW_SUBFOLDER,
            callback_data=FolderCb(action="new", id=folder.id).pack(),
        ),
        InlineKeyboardButton(
            text=messages.LBL_RENAME,
            callback_data=FolderCb(action="rename", id=folder.id).pack(),
        ),
    )
    b.row(
        InlineKeyboardButton(
            text=messages.LBL_DELETE,
            callback_data=FolderCb(action="del", id=folder.id).pack(),
        ),
        InlineKeyboardButton(
            text=messages.LBL_BACK,
            callback_data=FolderCb(
                action="open", id=folder.parent_id
            ).pack() if folder.parent_id else FolderCb(action="root").pack(),
        ),
    )
    return b.as_markup()


def build_confirm_folder_delete(folder_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(
            text=messages.LBL_YES,
            callback_data=FolderCb(action="delok", id=folder_id).pack(),
        ),
        InlineKeyboardButton(
            text=messages.LBL_NO,
            callback_data=FolderCb(action="open", id=folder_id).pack(),
        ),
    )
    return b.as_markup()


def build_folder_picker(folders: list[Folder]) -> InlineKeyboardMarkup:
    """Pick a target folder when moving a media (or 'uncategorised')."""
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(
            text=messages.LBL_NO_FOLDER, callback_data=FolderPickCb(id=0).pack()
        )
    )
    for folder in folders:
        b.row(
            InlineKeyboardButton(
                text=f"📁 {folder.name}",
                callback_data=FolderPickCb(id=folder.id).pack(),
            )
        )
    return b.as_markup()


def build_search_results(
    items: list[Media], page: int, total_pages: int
) -> InlineKeyboardMarkup:
    """One button per hit (opens manage) + a prev/next row using SearchCb."""
    b = InlineKeyboardBuilder()
    for media in items:
        b.row(
            InlineKeyboardButton(
                text=messages.file_row_label(media.code, _media_type(media)),
                callback_data=MediaCb(action="manage", id=media.id, page=0).pack(),
            )
        )
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(
                text=messages.LBL_PREV, callback_data=SearchCb(page=page - 1).pack()
            )
        )
    if page < total_pages - 1:
        nav.append(
            InlineKeyboardButton(
                text=messages.LBL_NEXT, callback_data=SearchCb(page=page + 1).pack()
            )
        )
    if nav:
        b.row(*nav)
    return b.as_markup()


def build_review_list(
    items: list[Media], page: int, total_pages: int
) -> InlineKeyboardMarkup:
    """One row per pending media: view / approve / reject + a nav row."""
    builder = InlineKeyboardBuilder()
    for media in items:
        builder.row(
            InlineKeyboardButton(
                text=messages.review_item_label(
                    media.code, _media_type(media), media.owner_user_id
                ),
                callback_data=ReviewCb(action="view", id=media.id, page=page).pack(),
            )
        )
        builder.row(
            InlineKeyboardButton(
                text=messages.LBL_APPROVE,
                callback_data=ReviewCb(action="approve", id=media.id, page=page).pack(),
            ),
            InlineKeyboardButton(
                text=messages.LBL_REJECT,
                callback_data=ReviewCb(action="reject", id=media.id, page=page).pack(),
            ),
        )
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(
                text=messages.LBL_PREV,
                callback_data=ReviewCb(action="list", page=page - 1).pack(),
            )
        )
    if page < total_pages - 1:
        nav.append(
            InlineKeyboardButton(
                text=messages.LBL_NEXT,
                callback_data=ReviewCb(action="list", page=page + 1).pack(),
            )
        )
    if nav:
        builder.row(*nav)
    return builder.as_markup()


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
            text=messages.lbl_password(media.password_hash is not None),
            callback_data=MediaCb(action="setpw", id=mid, page=page).pack(),
        ),
        InlineKeyboardButton(
            text=messages.LBL_MOVE_FOLDER,
            callback_data=MediaCb(action="movefolder", id=mid, page=page).pack(),
        ),
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


def build_delivered_actions(share_url: str, media_id: int) -> InlineKeyboardMarkup:
    """Under a delivered file: share link + a 🚩 report button."""
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text=messages.SHARE_BUTTON, url=share_url))
    b.row(
        InlineKeyboardButton(
            text=messages.REPORT_BUTTON,
            callback_data=ReportCb(action="start", id=media_id).pack(),
        )
    )
    return b.as_markup()


def build_report_reasons(media_id: int) -> InlineKeyboardMarkup:
    from app.models.media_report import REPORT_REASONS

    b = InlineKeyboardBuilder()
    for key in REPORT_REASONS:
        b.row(
            InlineKeyboardButton(
                text=messages.report_reason_title(key),
                callback_data=ReportCb(action="reason", id=media_id, value=key).pack(),
            )
        )
    return b.as_markup()


def build_url_button(text: str, url: str) -> InlineKeyboardMarkup:
    """A single URL button (used by ads for their tracked click link)."""
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text=text, url=url))
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
    b.row(
        InlineKeyboardButton(
            text=messages.BTN_INVOICES, callback_data=WalletCb(action="inv").pack()
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


def build_buy_confirm(
    plan_key: str, centralpay: bool = False, stars: bool = False
) -> InlineKeyboardMarkup:
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
    if stars:
        b.row(
            InlineKeyboardButton(
                text=messages.BTN_PAY_STARS,
                callback_data=StarsBuyCb(plan=plan_key).pack(),
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


def build_provider_choice(
    providers: list[str], *, amount: int = 0, plan: str = ""
) -> InlineKeyboardMarkup:
    """One button per enabled gateway (shown when more than one is enabled)."""
    b = InlineKeyboardBuilder()
    for key in providers:
        b.row(
            InlineKeyboardButton(
                text=messages.provider_title(key),
                callback_data=GateCb(provider=key, amount=amount, plan=plan).pack(),
            )
        )
    return b.as_markup()
