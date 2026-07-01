"""All user-facing Persian strings live here (centralized)."""
from __future__ import annotations

# --- exact strings required by the spec -------------------------------------
NOT_FOUND = "فایل مورد نظر پیدا نشد."
INACTIVE = "این فایل غیرفعال شده است."
LIMIT_REACHED = "محدودیت دانلود این فایل تکمیل شده است."
NOT_ADMIN_UPLOAD = "در حال حاضر فقط ادمین‌ها امکان آپلود فایل دارند."

# --- friendly welcome / errors ----------------------------------------------
WELCOME = (
    "سلام! 👋\n"
    "به ربات آپلودر خوش آمدید.\n\n"
    "برای دریافت فایل، روی لینک اختصاصی فایل کلیک کنید یا کد آن را باز کنید."
)

HELP = (
    "🤖 راهنما\n"
    "برای دریافت فایل، روی لینک اختصاصی آن کلیک کنید یا کد فایل را از طریق لینک باز کنید.\n"
    "اگر ادمین هستید، کافی است فایل/عکس/ویدیو را برای ربات بفرستید تا لینک اختصاصی ساخته شود.\n\n"
    "دستورها:\n"
    "/start — شروع"
)

GENERIC_ERROR = "متأسفانه در ارسال فایل خطایی رخ داد. لطفاً بعداً دوباره تلاش کنید."

UNSUPPORTED_UPLOAD = (
    "این نوع پیام پشتیبانی نمی‌شود. لطفاً یک فایل، عکس، ویدیو، صوت یا استیکر بفرستید."
)


def upload_success(deep_link: str, code: str) -> str:
    """Success message shown to an admin after a successful upload."""
    return (
        "✅ فایل با موفقیت ذخیره شد.\n\n"
        f"🔗 لینک اختصاصی:\n{deep_link}\n\n"
        f"🆔 کد فایل: {code}"
    )


def auto_delete_notice(seconds: int) -> str:
    """Notice shown before scheduling auto-deletion of delivered files."""
    if seconds % 3600 == 0 and seconds >= 3600:
        human = f"{seconds // 3600} ساعت"
    elif seconds % 60 == 0 and seconds >= 60:
        human = f"{seconds // 60} دقیقه"
    else:
        human = f"{seconds} ثانیه"
    return (
        f"⏳ توجه: این فایل پس از {human} به‌صورت خودکار حذف خواهد شد.\n"
        "لطفاً در صورت نیاز آن را ذخیره کنید."
    )


# ===========================================================================
# Admin panel (Phase 2)
# ===========================================================================

# --- reply keyboard button texts (kept identical in keyboard + handlers) -----
BTN_UPLOAD = "📤 آپلود فایل"
BTN_MY_FILES = "📁 فایل‌های من"
BTN_STATS = "📊 آمار"
BTN_SETTINGS = "⚙️ تنظیمات"

# --- panel / prompts ---------------------------------------------------------
ADMIN_PANEL = "پنل مدیریت ✨\nاز دکمه‌های زیر استفاده کنید."
UPLOAD_PROMPT = "یک فایل بفرست تا لینک دریافت کنی."
NO_FILES = "هنوز فایلی آپلود نکرده‌اید."
NOT_OWNED = "این فایل یافت نشد یا متعلق به شما نیست."

ASK_LIMIT = "عدد محدودیت دانلود را بفرست (۰ = نامحدود):"
ASK_AUTODELETE = "زمان حذف خودکار بر حسب ثانیه را بفرست (۰ = خاموش):"
ASK_CAPTION = "کپشن جدید را بفرست (برای پاک‌کردن «-» بفرست):"
INVALID_NUMBER = "لطفاً یک عدد صحیح نامنفی بفرست."

LIMIT_SET = "محدودیت دانلود به‌روزرسانی شد. ✅"
AUTODELETE_SET = "حذف خودکار به‌روزرسانی شد. ✅"
CAPTION_SET = "کپشن به‌روزرسانی شد. ✅"
ACTIVE_SET = "وضعیت فایل به‌روزرسانی شد. ✅"
PROTECT_SET = "محافظت محتوا به‌روزرسانی شد. ✅"
DELETE_CONFIRM = "آیا از حذف این فایل مطمئنید؟ این عمل قابل بازگشت نیست."
DELETED = "فایل حذف شد."
SETTINGS_SAVED = "تنظیمات ذخیره شد. ✅"

# --- inline button labels ----------------------------------------------------
LBL_AUTODEL = "⏱ حذف خودکار"
LBL_SETLIMIT = "🔢 محدودیت دانلود"
LBL_EDITCAP = "✏️ ویرایش کپشن"
LBL_LINK = "🔗 لینک"
LBL_STATS = "📊 آمار"
LBL_DELETE = "🗑 حذف"
LBL_BACK = "◀️ بازگشت"
LBL_PREV = "◀️"
LBL_NEXT = "▶️"
LBL_YES = "بله"
LBL_NO = "خیر"
SHARE_BUTTON = "🔗 اشتراک‌گذاری"
UNKNOWN_TYPE = "?"


def lbl_active(is_active: bool) -> str:
    return "وضعیت: ✅ فعال" if is_active else "وضعیت: ⛔️ غیرفعال"


def lbl_protect(protect: bool) -> str:
    return "محافظت محتوا: 🔒 روشن" if protect else "محافظت محتوا: 🔓 خاموش"


def lbl_set_protect(on: bool) -> str:
    return f"محافظت محتوا پیش‌فرض: {'روشن' if on else 'خاموش'}"


def lbl_set_autodel(seconds: int) -> str:
    human = f"{seconds} ثانیه" if seconds else "خاموش"
    return f"حذف خودکار پیش‌فرض: {human}"


def file_row_label(code: str, file_type: str) -> str:
    return f"🔗 {code} · {file_type}"


def files_list_header(total: int, page: int, pages: int) -> str:
    return f"📁 فایل‌های شما ({total}) — صفحه {page}/{pages}"


def owner_stats_view(media_count: int, total_downloads: int) -> str:
    return (
        "📊 آمار شما\n\n"
        f"تعداد فایل‌ها: {media_count}\n"
        f"مجموع دانلودها: {total_downloads}"
    )


def _fmt_limit(download_limit: int | None) -> str:
    return str(download_limit) if download_limit is not None else "نامحدود"


def _fmt_autodel(seconds: int | None) -> str:
    return f"{seconds} ثانیه" if seconds else "خاموش"


def manage_view(
    *,
    code: str,
    file_type: str,
    is_active: bool,
    protect_content: bool,
    auto_delete_seconds: int | None,
    download_count: int,
    download_limit: int | None,
) -> str:
    return (
        "🗂 مدیریت فایل\n\n"
        f"کد: {code}\n"
        f"نوع: {file_type}\n"
        f"وضعیت: {'✅ فعال' if is_active else '⛔️ غیرفعال'}\n"
        f"محافظت محتوا: {'🔒 روشن' if protect_content else '🔓 خاموش'}\n"
        f"حذف خودکار: {_fmt_autodel(auto_delete_seconds)}\n"
        f"دانلود: {download_count} / {_fmt_limit(download_limit)}"
    )


def file_stats_view(code: str, download_count: int, download_limit: int | None) -> str:
    return (
        f"📊 آمار فایل {code}\n\n"
        f"تعداد دانلود: {download_count}\n"
        f"محدودیت: {_fmt_limit(download_limit)}"
    )


def settings_view(protect: bool, seconds: int) -> str:
    return (
        "⚙️ تنظیمات پیش‌فرض\n\n"
        f"محافظت محتوا: {'روشن' if protect else 'خاموش'}\n"
        f"حذف خودکار: {_fmt_autodel(seconds)}\n\n"
        "برای تغییر، از دکمه‌های زیر استفاده کنید."
    )


def share_link(url: str) -> str:
    return f"🔗 لینک اشتراک‌گذاری:\n{url}"


# ===========================================================================
# Phase 2: force-join, batch upload, admin management, broadcast
# ===========================================================================

# --- reply keyboard (batch for admins; last three owners only) ---------------
BTN_BATCH_UPLOAD = "📚 آپلود گروهی"
BTN_CHANNELS = "🔐 عضویت اجباری"
BTN_ADMINS = "👥 ادمین‌ها"
BTN_BROADCAST = "📢 پیام همگانی"

# --- 1) force-join gate ------------------------------------------------------
GATE_PROMPT = (
    "برای دریافت این فایل ابتدا در کانال‌(های) زیر عضو شوید،\n"
    "سپس روی «عضو شدم، بررسی کن» بزنید:"
)
GATE_RECHECK_BTN = "✅ عضو شدم، بررسی کن"
GATE_STILL = "هنوز عضو همهٔ کانال‌ها نشده‌اید."


def channel_join_label(title: str | None, chat_id: str) -> str:
    return f"📢 {title or chat_id}"


# --- channel management (owners) ---------------------------------------------
CHANNELS_HEADER = "🔐 کانال‌های عضویت اجباری:"
CHANNELS_EMPTY = "هیچ کانال اجباری تعریف نشده است."
BTN_ADD_CHANNEL = "➕ افزودن کانال"
ASK_CHANNEL = (
    "آیدی کانال را به‌صورت @username بفرست، یا یک پیام از آن کانال را فوروارد کن.\n"
    "توجه: ربات باید در آن کانال ادمین باشد."
)
CHANNEL_ADDED = "کانال اضافه شد. ✅"
CHANNEL_NOT_ADMIN_WARN = (
    "⚠️ به نظر می‌رسد ربات در این کانال ادمین نیست؛ بررسی عضویت ممکن است کار نکند."
)
CHANNEL_INVALID = (
    "کانال نامعتبر است. یک @username معتبر بفرست یا پیامی از کانال فوروارد کن."
)
CHANNEL_REMOVED = "کانال حذف شد."


def channel_row_label(title: str | None, chat_id: str, is_active: bool) -> str:
    return f"{'✅' if is_active else '⛔️'} {title or chat_id}"


# --- 2) batch upload ---------------------------------------------------------
BATCH_START = (
    "حالت آپلود گروهی فعال شد.\nفایل‌ها را یکی‌یکی بفرست، سپس «پایان» را بزن."
)
BTN_BATCH_FINISH = "✅ پایان و ساخت لینک"
BTN_BATCH_CANCEL = "❌ لغو"
BATCH_EMPTY = "هیچ فایلی اضافه نشده است. یک فایل بفرست یا «لغو» را بزن."
BATCH_HINT = "فایل بفرست یا «پایان» را بزن."
BATCH_CANCELLED = "آپلود گروهی لغو شد."


def batch_added(count: int) -> str:
    return f"{count} فایل اضافه شد."


def batch_done(deep_link: str, code: str, count: int) -> str:
    return f"✅ {count} فایل ذخیره شد.\n\n🔗 لینک:\n{deep_link}\n\n🆔 کد: {code}"


# --- 3) admin management (owners) --------------------------------------------
ADMINS_HEADER = "👥 ادمین‌ها:"
ADMINS_EMPTY = "هیچ ادمینی ثبت نشده است."
BTN_ADD_ADMIN = "➕ افزودن ادمین"
ASK_ADMIN = "آیدی عددی تلگرام کاربر را بفرست، یا یک پیام از او فوروارد کن."
ADMIN_ADDED = "ادمین اضافه شد. ✅"
ADMIN_REMOVED = "ادمین حذف شد."
ADMIN_INVALID = "آیدی نامعتبر است. یک عدد بفرست یا پیامی از کاربر فوروارد کن."
ERR_CANNOT_SELF = "نمی‌توانید خودتان را حذف یا غیرفعال کنید."
ERR_CANNOT_ENV_OWNER = (
    "این کاربر از طریق تنظیمات سرور مالک است و قابل حذف/غیرفعال‌سازی نیست."
)


def admin_row_label(telegram_id: int, role: str, is_active: bool) -> str:
    return f"{'✅' if is_active else '⛔️'} {telegram_id} · {role}"


# --- 4) broadcast (owners) ---------------------------------------------------
BROADCAST_ASK = (
    "پیام همگانی را بفرست (متن، عکس، ویدیو و ...).\nبرای انصراف /panel را بزن."
)
BROADCAST_NO_MESSAGE = "پیامی برای ارسال یافت نشد."
BROADCAST_STARTED = "ارسال آغاز شد؛ نتیجه پس از اتمام اعلام می‌شود."
BROADCAST_CANCELLED = "ارسال لغو شد."


def broadcast_confirm(count: int) -> str:
    return f"ارسال به {count} کاربر؟"


def broadcast_summary(sent: int, failed: int) -> str:
    return f"📢 ارسال شد: {sent} | ناموفق/مسدود: {failed}"
