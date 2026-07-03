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


# --- user uploads + review (B1) ---------------------------------------------
UPLOAD_PENDING_REVIEW = (
    "✅ فایل شما ثبت شد و پس از تأیید ادمین، لینک آن برایتان ارسال می‌شود. ⏳"
)


def upload_approved_notify(deep_link: str, code: str) -> str:
    return (
        "✅ فایل شما تأیید شد!\n\n"
        f"🔗 لینک اختصاصی:\n{deep_link}\n\n"
        f"🆔 کد فایل: {code}"
    )


def upload_rejected_notify(reason: str | None) -> str:
    base = "❌ متأسفانه فایل شما تأیید نشد."
    return f"{base}\n\nدلیل: {reason}" if reason else base


# review queue (admins)
BTN_REVIEW = "🕵️ بازبینی آپلودها"
REVIEW_QUEUE_EMPTY = "هیچ فایلی در انتظار بازبینی نیست."
LBL_APPROVE = "✅ تأیید"
LBL_REJECT = "❌ رد"
ASK_REJECT_REASON = "دلیل رد را بنویس (برای رد بدون دلیل «-» بفرست):"
REVIEW_APPROVED = "فایل تأیید شد و به کاربر اطلاع داده شد. ✅"
REVIEW_REJECTED = "فایل رد شد و به کاربر اطلاع داده شد."
REVIEW_GONE = "این مورد دیگر در صف بازبینی نیست."


def review_queue_header(total: int, page: int, pages: int) -> str:
    return f"🕵️ صف بازبینی ({total}) — صفحه {page}/{pages}"


def review_item_label(code: str, file_type: str, owner_id: int | None) -> str:
    owner = f" · کاربر #{owner_id}" if owner_id else ""
    return f"👁 {code} · {file_type}{owner}"


# --- folders (B2) -----------------------------------------------------------
BTN_FOLDERS = "📂 پوشه‌ها"
FOLDERS_ROOT_HEADER = "📂 پوشه‌ها\nیک پوشه را باز کنید یا پوشهٔ جدیدی بسازید."
FOLDERS_EMPTY = "هنوز پوشه‌ای ساخته نشده. با دکمهٔ زیر یکی بسازید."
LBL_NEW_FOLDER = "➕ پوشهٔ جدید"
LBL_NEW_SUBFOLDER = "➕ زیرپوشه"
LBL_RENAME = "✏️ تغییر نام"
LBL_NO_FOLDER = "🚫 بدون پوشه"
LBL_MOVE_FOLDER = "📂 انتقال به پوشه"
ASK_FOLDER_NAME = "نام پوشه را بفرست:"
ASK_FOLDER_RENAME = "نام جدید پوشه را بفرست:"
FOLDER_CREATED = "پوشه ساخته شد. ✅"
FOLDER_RENAMED = "نام پوشه تغییر کرد. ✅"
FOLDER_DELETED = "پوشه حذف شد (فایل‌های داخل آن حفظ شدند)."
FOLDER_HAS_CHILDREN = "این پوشه زیرپوشه دارد؛ ابتدا زیرپوشه‌ها را حذف کنید."
FOLDER_GONE = "این پوشه دیگر وجود ندارد."
CHOOSE_TARGET_FOLDER = "این فایل به کدام پوشه منتقل شود؟"
MEDIA_MOVED = "فایل به پوشهٔ موردنظر منتقل شد. ✅"


def folder_view_header(name: str, sub_count: int, media_count: int) -> str:
    return f"📁 {name}\nزیرپوشه‌ها: {sub_count} · فایل‌ها: {media_count}"


# --- search (B3) ------------------------------------------------------------
BTN_SEARCH = "🔍 جستجو"
ASK_SEARCH_QUERY = "عبارت جستجو را بفرست (کد، عنوان، کپشن یا نام فایل):"
SEARCH_EMPTY = "نتیجه‌ای یافت نشد."
SEARCH_DISABLED = "جستجو در حال حاضر فعال نیست."


def search_results_header(total: int, page: int, pages: int) -> str:
    return f"🔍 نتایج ({total}) — صفحه {page}/{pages}"


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
MY_FILES_HEADER = "📁 فایل‌های شما:"
NOT_OWNED = "این فایل یافت نشد یا متعلق به شما نیست."

ASK_LIMIT = "عدد محدودیت دانلود را بفرست (۰ = نامحدود):"
ASK_AUTODELETE = "زمان حذف خودکار بر حسب ثانیه را بفرست (۰ = خاموش):"
ASK_CAPTION = "کپشن جدید را بفرست (برای پاک‌کردن «-» بفرست):"
ASK_MEDIA_PASSWORD = "گذرواژهٔ جدید این فایل را بفرست (برای حذف گذرواژه «-» بفرست):"
INVALID_NUMBER = "لطفاً یک عدد صحیح نامنفی بفرست."

LIMIT_SET = "محدودیت دانلود به‌روزرسانی شد. ✅"
AUTODELETE_SET = "حذف خودکار به‌روزرسانی شد. ✅"
CAPTION_SET = "کپشن به‌روزرسانی شد. ✅"
MEDIA_PASSWORD_SET = "گذرواژهٔ فایل تنظیم شد. 🔒"
MEDIA_PASSWORD_REMOVED = "گذرواژهٔ فایل حذف شد. 🔓"
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


def lbl_password(has_password: bool) -> str:
    return "🔑 گذرواژه: 🔒 دارد" if has_password else "🔑 گذرواژه: 🔓 ندارد"


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
    has_password: bool = False,
) -> str:
    return (
        "🗂 مدیریت فایل\n\n"
        f"کد: {code}\n"
        f"نوع: {file_type}\n"
        f"وضعیت: {'✅ فعال' if is_active else '⛔️ غیرفعال'}\n"
        f"محافظت محتوا: {'🔒 روشن' if protect_content else '🔓 خاموش'}\n"
        f"گذرواژه: {'🔒 دارد' if has_password else '🔓 ندارد'}\n"
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


# --- per-file password gate (viewer side) ------------------------------------
PASSWORD_PROMPT = "🔑 این فایل با گذرواژه محافظت می‌شود. لطفاً گذرواژه را بفرست:"
PASSWORD_LOCKED = (
    "⛔️ به دلیل تلاش‌های ناموفق زیاد، دریافت این فایل موقتاً برای شما مسدود شد. "
    "لطفاً کمی بعد دوباره تلاش کنید."
)


def password_wrong(remaining: int) -> str:
    return f"❌ گذرواژه نادرست است. {remaining} تلاش باقی مانده."


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


def broadcast_summary(sent: int, failed: int, blocked: int = 0) -> str:
    return f"📢 ارسال شد: {sent} | ناموفق: {failed} | مسدود: {blocked}"


# ===========================================================================
# Phase 3: wallet, top-up, plans, subscriptions, feature gating
# ===========================================================================

# --- reply keyboard buttons --------------------------------------------------
BTN_WALLET = "💳 کیف پول"
BTN_SUBSCRIPTION = "⭐️ اشتراک"
BTN_SELL = "💼 فروش"  # owners only


def _toman(amount: int) -> str:
    return f"{amount:,} تومان"


# --- wallet ------------------------------------------------------------------
def wallet_view(balance: int) -> str:
    return f"💳 موجودی شما: {_toman(balance)}"


BTN_TOPUP = "➕ افزایش موجودی"
BTN_TRANSACTIONS = "📜 تراکنش‌ها"
ASK_TOPUP_AMOUNT = "مبلغ افزایش موجودی را به تومان بفرست (فقط عدد):"
INVALID_AMOUNT = "مبلغ نامعتبر است. یک عدد معتبر (بزرگ‌تر از حداقل) بفرست."
PAYMENT_DISABLED = "پرداخت موقتاً غیرفعال است."
TOPUP_PENDING = "رسید شما ثبت شد و در انتظار تأیید است. ✅"
TRANSACTIONS_EMPTY = "تراکنشی وجود ندارد."


def min_amount_hint(minimum: int) -> str:
    return f"حداقل مبلغ: {_toman(minimum)}"


def topup_instructions(card_number: str, card_holder: str, amount: int) -> str:
    return (
        f"مبلغ {_toman(amount)} را به کارت زیر واریز کنید:\n\n"
        f"💳 {card_number}\n"
        f"👤 {card_holder}\n\n"
        "سپس رسید پرداخت (عکس) یا کد پیگیری را همین‌جا بفرستید."
    )


_TXN_LABELS = {
    "deposit": "واریز",
    "purchase": "خرید",
    "refund": "بازگشت وجه",
    "adjustment": "تعدیل",
}


def transactions_view(rows: list) -> str:
    if not rows:
        return TRANSACTIONS_EMPTY
    lines = ["📜 آخرین تراکنش‌ها:"]
    for tx in rows:
        sign = "➕" if tx.amount >= 0 else "➖"
        label = _TXN_LABELS.get(tx.type, tx.type)
        lines.append(f"{sign} {abs(tx.amount):,} — {label} (مانده: {tx.balance_after:,})")
    return "\n".join(lines)


# --- subscription ------------------------------------------------------------
def subscription_view(plan: str, expires: str | None) -> str:
    title = {"free": "رایگان", "plus": "پلاس", "max": "مکس"}.get(plan, plan)
    body = f"⭐️ پلن فعلی شما: {title}"
    if expires:
        body += f"\n⏳ انقضا: {expires}"
    return body + "\n\nپلن‌های قابل خرید:"


def plan_button_label(title: str, price: int, duration_days: int) -> str:
    price_txt = "رایگان" if price <= 0 else f"{price:,} ت"
    dur_txt = "بدون انقضا" if duration_days == 0 else f"{duration_days} روز"
    return f"{title} — {price_txt} / {dur_txt}"


BTN_BUY = "🛒 خرید"


def buy_confirm(title: str, price: int) -> str:
    price_txt = "رایگان" if price <= 0 else _toman(price)
    return f"خرید پلن «{title}» به مبلغ {price_txt}؟"


def plan_activated(expires: str | None) -> str:
    if expires:
        return f"✅ پلن شما فعال شد تا {expires}."
    return "✅ پلن شما فعال شد (بدون انقضا)."


def insufficient_funds(balance: int, price: int) -> str:
    return (
        "موجودی کافی نیست.\n"
        f"موجودی: {_toman(balance)} | قیمت: {_toman(price)}\n"
        "ابتدا کیف پول را شارژ کنید."
    )


PLAN_NOT_AVAILABLE = "این پلن در دسترس نیست."
PURCHASE_IN_PROGRESS = "درخواست قبلی شما در حال پردازش است. لطفاً چند لحظه صبر کنید."
PURCHASE_FAILED = "خطا در انجام خرید. مبلغی کسر نشد؛ لطفاً دوباره تلاش کنید."

# --- feature gating ----------------------------------------------------------
_PLAN_TITLES = {"free": "رایگان", "plus": "پلاس", "max": "مکس"}


def requires_plan(plan_key: str) -> str:
    return f"این قابلیت نیاز به پلن «{_PLAN_TITLES.get(plan_key, plan_key)}» دارد."


def file_limit_reached(limit: int) -> str:
    return (
        f"به سقف تعداد فایل ({limit}) در پلن فعلی رسیده‌اید.\n"
        "برای افزایش سقف، پلن خود را ارتقا دهید."
    )


BTN_OPEN_PLANS = "⭐️ مشاهده پلن‌ها"

# --- owner: payments ---------------------------------------------------------
PAY_APPROVE = "✅ تأیید"
PAY_REJECT = "❌ رد"
PAY_ALREADY = "قبلاً تأیید شده."
PAY_APPROVED = "پرداخت تأیید شد. ✅"
PAY_REJECTED = "پرداخت رد شد."


def payment_notify(user_id: int, amount: int, method: str, payment_id: int) -> str:
    return (
        "💳 درخواست شارژ جدید\n\n"
        f"کاربر: {user_id}\n"
        f"مبلغ: {_toman(amount)}\n"
        f"روش: {method}\n"
        f"شناسه: {payment_id}"
    )


def user_credited(amount: int) -> str:
    return f"✅ کیف پول شما {_toman(amount)} شارژ شد."


USER_PAYMENT_REJECTED = "❌ پرداخت شما تأیید نشد. در صورت واریز، با پشتیبانی تماس بگیرید."

# --- owner: sell settings ----------------------------------------------------
def sell_view(card_number: str | None, card_holder: str | None) -> str:
    return (
        "💼 تنظیمات فروش\n\n"
        f"شمارهٔ کارت: {card_number or '—'}\n"
        f"صاحب کارت: {card_holder or '—'}\n\n"
        "قیمت و مدت هر پلن را نیز می‌توانید تنظیم کنید."
    )


BTN_SET_CARD = "شمارهٔ کارت"
BTN_SET_HOLDER = "نام صاحب کارت"
ASK_CARD = "شمارهٔ کارت را بفرست:"
ASK_HOLDER = "نام صاحب کارت را بفرست:"


def sell_price_label(title: str, price: int) -> str:
    price_txt = "رایگان" if price <= 0 else f"{price:,} ت"
    return f"💰 قیمت {title}: {price_txt}"


def sell_duration_label(title: str, days: int) -> str:
    return f"⏳ مدت {title}: {days} روز"


def ask_price(title: str) -> str:
    return f"قیمت پلن «{title}» را به تومان بفرست (عدد):"


def ask_duration(title: str) -> str:
    return f"مدت پلن «{title}» را به روز بفرست (۰ = بدون انقضا):"


SELL_SAVED = "ذخیره شد. ✅"


# ===========================================================================
# Phase 5: CentralPay online gateway
# ===========================================================================
BTN_PAY_CARD = "💳 کارت‌به‌کارت"
BTN_PAY_ONLINE = "🌐 پرداخت آنلاین"
BTN_PAY_WALLET = "👛 پرداخت از کیف پول"
BTN_PAY_LINK = "🌐 پرداخت"
BTN_PAY_CHECK = "🔄 بررسی پرداخت"

CHOOSE_TOPUP_METHOD = "روش افزایش موجودی را انتخاب کنید:"
CHOOSE_PAY_METHOD = "روش پرداخت را انتخاب کنید:"
ASK_ONLINE_AMOUNT = "مبلغ پرداخت آنلاین را به تومان بفرست (فقط عدد):"
CENTRALPAY_DISABLED = "پرداخت آنلاین در حال حاضر فعال نیست."
CENTRALPAY_START_FAILED = "ایجاد لینک پرداخت ناموفق بود. کمی بعد دوباره تلاش کنید."
CENTRALPAY_PENDING = (
    "لینک پرداخت ساخته شد. روی «پرداخت» بزنید و پس از پرداخت، «بررسی پرداخت» را بزنید."
)

# verify_and_apply result -> user message
CENTRALPAY_FAILED = "پرداختی یافت نشد یا هنوز کامل نشده است. اگر پرداخت کرده‌اید، کمی بعد دوباره «بررسی پرداخت» را بزنید."
CENTRALPAY_MISMATCH = "مغایرت در مبلغ پرداخت. لطفاً با پشتیبانی تماس بگیرید."


def centralpay_credited(balance: int) -> str:
    return f"✅ پرداخت با موفقیت انجام شد.\nموجودی جدید: {balance:,} تومان"


CENTRALPAY_ALREADY = "این پرداخت قبلاً تأیید و اعمال شده است. ✅"


# ===========================================================================
# Phase C1: pluggable gateways (the CENTRALPAY_* texts above are provider-
# neutral in wording and serve every gateway)
# ===========================================================================
CHOOSE_PAY_PROVIDER = "درگاه پرداخت را انتخاب کنید:"

_PROVIDER_TITLES = {
    "centralpay": "🏦 سنترال‌پی",
    "zarinpal": "💛 زرین‌پال",
    "zibal": "💜 زیبال",
}


def provider_title(key: str) -> str:
    return _PROVIDER_TITLES.get(key, key)


def gateway_description(order_id: int) -> str:
    """Shown on the gateway's payment page (Zarinpal/Zibal)."""
    return f"شارژ کیف پول — سفارش {order_id}"


# ===========================================================================
# Phase C2: ads
# ===========================================================================
def ad_view(title: str, text: str) -> str:
    return f"📣 {title}\n\n{text}"


BTN_ADS = "📣 تبلیغات"
ADS_EMPTY = "تبلیغی تعریف نشده. با دکمهٔ زیر یکی بسازید."
ADS_HEADER = "📣 تبلیغات (نمایش/کلیک) — مدیریت کامل در پنل وب."
LBL_NEW_AD = "➕ تبلیغ جدید"
ASK_AD_TITLE = "عنوان تبلیغ را بفرست:"
ASK_AD_TEXT = "متن تبلیغ را بفرست:"
ASK_AD_PLACEMENT = "جایگاه نمایش را انتخاب کن:"
AD_CREATED = "تبلیغ ساخته شد. ✅ (دکمه/پلن هدف/سقف نمایش را از پنل تنظیم کنید)"
AD_DELETED = "تبلیغ حذف شد."
AD_TOGGLED = "وضعیت تبلیغ تغییر کرد. ✅"
AD_GONE = "این تبلیغ دیگر وجود ندارد."

_PLACEMENT_TITLES = {
    "before_file": "قبل از فایل",
    "after_file": "بعد از فایل",
    "start_message": "پیام شروع",
}


def placement_title(key: str) -> str:
    return _PLACEMENT_TITLES.get(key, key)


def ad_row_label(ad_id: int, title: str, placement: str, active: bool,
                 impressions: int, clicks: int) -> str:
    state = "✅" if active else "⛔️"
    return f"{state} {title} · {placement_title(placement)} · 👁 {impressions} · 👆 {clicks}"


# ===========================================================================
# Phase C4: Telegram Stars
# ===========================================================================
# --- E1: licensing -------------------------------------------------------------
LICENSE_BLOCKED = (
    "⚠️ لایسنس این ربات معتبر نیست یا منقضی شده؛ این قابلیت موقتاً غیرفعال است.\n"
    "دریافت فایل‌ها برای کاربران فعال می‌ماند. لطفاً برای تمدید با فروشنده تماس بگیرید."
)

# --- D2: media abuse reports -------------------------------------------------
REPORT_BUTTON = "🚩 گزارش"
ASK_REPORT_REASON = "دلیل گزارش این فایل را انتخاب کنید:"
REPORT_THANKS = "گزارش شما ثبت شد؛ تیم مدیریت بررسی می‌کند. 🙏"
REPORT_DUPLICATE = "شما قبلاً این فایل را گزارش کرده‌اید."
REPORT_HELP = (
    "🚩 برای گزارش محتوای نامناسب، زیر همان فایل دریافت‌شده روی دکمهٔ «🚩 گزارش» بزنید "
    "و دلیل را انتخاب کنید تا تیم مدیریت بررسی کند."
)

# --- command menu ------------------------------------------------------------
BACKUP_POINTER = "🗄 پشتیبان‌گیری و بازیابی از پنل وب مدیریت انجام می‌شود (بخش «پشتیبان»)."

# --- G3: in-bot admin extras -------------------------------------------------
BTN_PANEL = "🖥 پنل مدیریت"
BTN_LOG_GROUP = "🪵 گروه لاگ"
PANEL_LINK_INTRO = (
    "🖥 برای بخش‌های حساس (کلید درگاه‌ها، تنظیمات، آمار) از لینک ورود امن زیر استفاده کنید.\n"
    "هر لینک فقط یک‌بار و برای چند دقیقه معتبر است و هیچ رمزی در چت نمایش داده نمی‌شود."
)
PANEL_LINK_NO_ACCOUNT = "برای این ربات هنوز حساب پنل ساخته نشده است."
LOG_GROUP_ASK = (
    "🪵 شناسهٔ عددی گروه لاگ (سوپرگروه با Topics) را بفرستید؛ مثال: -1001234567890\n"
    "ربات باید در آن گروه ادمین با دسترسی «مدیریت موضوعات» باشد. برای غیرفعال‌کردن، عدد 0 را بفرستید."
)
LOG_GROUP_SET = "✅ گروه لاگ ثبت شد. موضوع‌ها با اولین رویداد ساخته می‌شوند."
LOG_GROUP_CLEARED = "گروه لاگ غیرفعال شد."
LOG_GROUP_INVALID = "شناسهٔ نامعتبر. یک عدد صحیح بفرستید."


def panel_link_label(page: str) -> str:
    return {
        "providers": "درگاه‌ها و کلیدها",
        "settings": "تنظیمات",
        "stats": "آمار کامل",
        "dashboard": "داشبورد",
    }.get(page, page)


# --- F3: buy-a-bot factory ---------------------------------------------------
BTN_CREATE_BOT = "🤖 ساخت ربات"
NEWBOT_NO_PLANS = "در حال حاضر پلن فعالی برای ساخت ربات وجود ندارد."
NEWBOT_ASK_TOKEN = (
    "🤖 توکن ربات خود را از @BotFather بگیرید و همین‌جا ارسال کنید.\n"
    "توکن شما محرمانه ذخیره می‌شود و هرگز نمایش داده نمی‌شود."
)
NEWBOT_INVALID_TOKEN = "❌ توکن نامعتبر است. لطفاً توکن صحیح ربات را از @BotFather ارسال کنید."
NEWBOT_ALREADY = "این ربات قبلاً در سیستم ثبت شده است."
NEWBOT_INSUFFICIENT = "موجودی کیف پول شما کافی نیست. ابتدا کیف پول را شارژ کنید."
NEWBOT_FAILED = "خطا در ساخت ربات؛ مبلغی کسر نشد. لطفاً بعداً دوباره تلاش کنید."
NEWBOT_ONLY_PLATFORM = "این قابلیت فقط در ربات اصلی در دسترس است."


def newbot_plans_view(balance: int) -> str:
    return (
        f"🤖 ساخت ربات اختصاصی\n\nموجودی کیف پول شما: {balance:,} تومان\n\n"
        "یک پلن را انتخاب کنید:"
    )


def newbot_plan_label(title: str, price: int, duration_days: int) -> str:
    period = "دائمی" if duration_days == 0 else f"{duration_days} روزه"
    return f"{title} — {price:,} تومان ({period})"


def newbot_success(
    username: str | None, panel_url: str, expires_at,
    panel_username: str | None = None, panel_password: str | None = None,
) -> str:
    handle = f"@{username}" if username else "ربات شما"
    lines = [
        f"✅ ربات {handle} فعال شد!",
        "شما مالک این ربات هستید؛ با دستور /panel آن را مدیریت کنید.",
        f"پنل وب: {panel_url}",
    ]
    if panel_username and panel_password:
        lines.append(
            f"\nورود به پنل:\nنام کاربری: {panel_username}\nرمز عبور: {panel_password}\n"
            "(لطفاً پس از ورود، رمز عبور را تغییر دهید.)"
        )
    if expires_at is not None:
        lines.append(f"اعتبار تا: {expires_at:%Y-%m-%d}")
    return "\n".join(lines)

_REPORT_REASON_TITLES = {
    "copyright": "نقض کپی‌رایت",
    "inappropriate": "محتوای نامناسب",
    "spam": "اسپم/کلاه‌برداری",
    "other": "موارد دیگر",
}


def report_reason_title(key: str) -> str:
    return _REPORT_REASON_TITLES.get(key, key)


BTN_PAY_STARS = "⭐ پرداخت با استارز"
STARS_INVALID = "این پرداخت معتبر نیست یا پلن در دسترس نیست."
STARS_ALREADY = "این پرداخت قبلاً اعمال شده است. ✅"
STARS_FAILED = (
    "پرداخت شما دریافت شد اما فعال‌سازی خودکار انجام نشد؛ مبلغ به کیف پول شما "
    "اضافه شد و می‌توانید پلن را از کیف پول بخرید."
)


def stars_invoice_title(plan_title: str) -> str:
    return f"خرید پلن {plan_title}"


def stars_invoice_description(plan_title: str, days: int) -> str:
    period = f"{days}‌روزه" if days else "دائمی"
    return f"اشتراک {period} پلن {plan_title}"


# --- Phase H2: in-bot support / ticketing -----------------------------------
BTN_SUPPORT = "🎧 پشتیبانی"

SUPPORT_INTRO_USER = (
    "🎧 پشتیبانی\n"
    "برای ثبت درخواست، یک موضوع کوتاه بفرست تا تیکت باز شود."
)
SUPPORT_INTRO_PLATFORM = (
    "🎧 پشتیبانی پلتفرم\n"
    "درخواستت به تیم پلتفرم ارسال می‌شود. یک موضوع کوتاه بفرست."
)
SUPPORT_ASK_SUBJECT = "موضوع تیکت را در یک خط بفرست:"
SUPPORT_ASK_MESSAGE = "متن پیام خود را بفرست:"
SUPPORT_CREATED = "✅ تیکت شما ثبت شد. پاسخ از همین‌جا اطلاع‌رسانی می‌شود."
SUPPORT_REPLY_SENT = "✅ پیام شما ثبت شد."
SUPPORT_CLOSED = "✅ تیکت بسته شد."
SUPPORT_EMPTY_BODY = "متن پیام نمی‌تواند خالی باشد."

SUPPORT_BTN_NEW_MSG = "➕ پیام جدید"
SUPPORT_BTN_CLOSE = "✅ بستن تیکت"

_SUPPORT_STATUS_FA = {"open": "باز", "answered": "پاسخ‌داده‌شده", "closed": "بسته"}


def support_status_fa(status: str) -> str:
    return _SUPPORT_STATUS_FA.get(status, status)


def support_ticket_view(subject: str, status: str, lines: list[str]) -> str:
    body = "\n".join(lines) if lines else "—"
    return (
        f"🎫 {subject}\n"
        f"وضعیت: {support_status_fa(status)}\n"
        f"— — —\n{body}"
    )


def support_admin_notify(subject: str) -> str:
    return (
        "🎧 پیام پشتیبانی جدید از یک کاربر.\n"
        f"موضوع: {subject}\n"
        "برای پاسخ به پنل مدیریت مراجعه کنید."
    )


def support_user_reply_notify(subject: str, body: str) -> str:
    return f"🎧 پاسخ پشتیبانی برای «{subject}»:\n\n{body}"


# --- Phase H4: invoices / receipts ------------------------------------------
BTN_INVOICES = "🧾 فاکتورها"

_INVOICE_KIND_FA = {
    "topup": "شارژ کیف پول", "plan": "خرید پلن",
    "bot_creation": "ساخت ربات", "rental": "تمدید ربات",
}
_INVOICE_METHOD_FA = {
    "card": "کارت‌به‌کارت", "wallet": "کیف پول", "stars": "استارز",
    "zarinpal": "زرین‌پال", "zibal": "زیبال", "centralpay": "سنترال‌پی",
}


def invoice_kind_fa(kind: str) -> str:
    return _INVOICE_KIND_FA.get(kind, kind)


def invoice_method_fa(method: str) -> str:
    return _INVOICE_METHOD_FA.get(method, method)


def invoice_receipt(*, invoice_no, kind, amount, method, ref=None, date=None) -> str:
    lines = [
        "🧾 رسید پرداخت",
        f"شماره فاکتور: {invoice_no}",
        f"بابت: {invoice_kind_fa(kind)}",
        f"مبلغ: {amount:,} تومان",
        f"روش: {invoice_method_fa(method)}",
    ]
    if ref:
        lines.append(f"کد پیگیری: {ref}")
    if date:
        lines.append(f"تاریخ: {date}")
    return "\n".join(lines)


def invoices_view(rows) -> str:
    if not rows:
        return "🧾 هنوز فاکتوری ثبت نشده است."
    out = ["🧾 فاکتورهای شما:"]
    for inv in rows:
        out.append(
            f"#{inv.invoice_no} · {invoice_kind_fa(inv.kind)} · "
            f"{inv.amount:,} تومان · {invoice_method_fa(inv.method)}"
        )
    return "\n".join(out)


# --- Phase I1: blocked users ------------------------------------------------
ACCOUNT_BLOCKED = "حساب شما مسدود شده است."


# --- Phase I3: panel manual wallet + subscription notices -------------------
def wallet_credited_notice(amount: int, reason: str) -> str:
    return f"💰 مبلغ {amount:,} تومان به کیف پول شما اضافه شد.\nبابت: {reason}"


def wallet_debited_notice(amount: int, reason: str) -> str:
    return f"💳 مبلغ {amount:,} تومان از کیف پول شما کسر شد.\nبابت: {reason}"


def subscription_changed_notice(plan: str, expires: str | None) -> str:
    tail = f" تا {expires}" if expires else " (دائمی)"
    return f"⭐️ اشتراک شما به «{plan}»{tail} تغییر کرد."


SUBSCRIPTION_CANCELLED_NOTICE = "⛔️ اشتراک شما لغو شد."


# --- Phase I4: Telegram Stars toggle ----------------------------------------
STARS_DISABLED = "پرداخت با استارز در حال حاضر غیرفعال است."


# --- Phase J1: reactions + favorites + sorted views --------------------------
LBL_FAVORITE = "⭐ ذخیره"
BTN_FAVORITES = "⭐ علاقه‌مندی‌ها"

REACT_SET = {"like": "👍 پسندیدی", "dislike": "👎 نپسندیدی", "favorite": "⭐ ذخیره شد"}
REACT_CLEARED = {"like": "لغو شد", "dislike": "لغو شد", "favorite": "از علاقه‌مندی‌ها حذف شد"}
FAVORITES_EMPTY = "هنوز چیزی ذخیره نکرده‌ای. زیر هر فایل روی «⭐ ذخیره» بزن."
BROWSE_DISABLED = "مرور عمومی فایل‌ها در حال حاضر فعال نیست."

_BROWSE_TITLES = {
    "favs": "⭐ علاقه‌مندی‌های تو",
    "popular": "🔥 محبوب‌ترین‌ها",
    "newest": "🆕 جدیدترین‌ها",
    "most_viewed": "👁 پربازدیدترین‌ها",
}


def browse_header(sort: str, page: int) -> str:
    title = _BROWSE_TITLES.get(sort, sort)
    return f"{title} — صفحه {page + 1}"


def browse_row(code: str, title: str | None, likes: int, downloads: int) -> str:
    name = title or code
    return f"{name} · 👍 {likes} · ⬇️ {downloads}"


# --- Phase J2: inline search --------------------------------------------------
def inline_result_description(downloads: int, likes: int) -> str:
    return f"⬇️ {downloads} · 👍 {likes}"


def inline_result_message(name: str, link: str) -> str:
    return f"📁 {name}\n{link}"


# --- Phase J4: video thumbnail / cover ---------------------------------------
ASK_THUMBNAIL = "عکس کاور را بفرست (برای حذف کاور «-» بفرست):"
THUMBNAIL_SET = "کاور فایل تنظیم شد. 🖼"
THUMBNAIL_CLEARED = "کاور فایل حذف شد."


def lbl_thumbnail(has_thumbnail: bool) -> str:
    return "🖼 کاور: دارد" if has_thumbnail else "🖼 کاور"


# --- Phase J5: channel preview -------------------------------------------------
BTN_GET_FILE = "📥 دریافت فایل"


def preview_post(name: str) -> str:
    return f"🎬 {name}\n\nبرای دریافت روی دکمهٔ زیر بزنید:"
