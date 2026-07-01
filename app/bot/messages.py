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
