# مرجع کامل متغیرهای محیطی

همهٔ پیکربندی از فایل `.env` در ریشهٔ پروژه خوانده می‌شود (الگو: `.env.example`).
پس از هر تغییر در `.env` سرویس‌ها را ری‌استارت کنید:

```bash
docker compose up -d --force-recreate api bot worker
```

> ⚠️ فایل `.env` حاوی اسرار است؛ هرگز آن را commit یا برای کسی ارسال نکنید.
> مقادیر پیش‌فرضِ `change_this_*` را حتماً قبل از استقرار عوض کنید.

## عمومی

| متغیر | الزامی؟ | پیش‌فرض | توضیح |
|---|---|---|---|
| `PROJECT_NAME` | خیر | `ZedUploader` | نام پروژه؛ در لاگ‌ها و عنوان API نمایش داده می‌شود. |
| `LOG_LEVEL` | خیر | `INFO` | سطح لاگ (`DEBUG`، `INFO`، `WARNING`، `ERROR`). |

## ربات تلگرام

| متغیر | الزامی؟ | پیش‌فرض | توضیح |
|---|---|---|---|
| `BOT_TOKEN` | **بله** | — | توکن ربات از [@BotFather](https://t.me/BotFather). محرمانه است. |
| `BOT_USERNAME` | **بله** | `your_bot_username` | یوزرنیم ربات بدون `@`؛ برای ساخت لینک‌های عمیق (`t.me/بات?start=کد`) استفاده می‌شود. |
| `ADMIN_IDS` | **بله** | — | شناسه‌های عددی تلگرامِ مالک/ادمین‌ها، جداشده با کاما (مثال: `111,222`). مقادیر غیرعددی نادیده گرفته می‌شوند. |
| `BOT_MODE` | خیر | `webhook` | حالت دریافت آپدیت‌ها: `webhook` (production) یا `polling` (توسعه/بدون دامنه). جزئیات: [webhook-polling.md](webhook-polling.md) |

## دامنه و Webhook

| متغیر | الزامی؟ | پیش‌فرض | توضیح |
|---|---|---|---|
| `DOMAIN` | در حالت webhook **بله** | `https://example.com` | آدرس عمومی HTTPS سرور (با `https://`). راهنما: [domain-ssl.md](domain-ssl.md) |
| `WEBHOOK_PATH` | خیر | `/telegram/webhook` | مسیر endpoint دریافت آپدیت از تلگرام. آدرس نهایی = `DOMAIN + WEBHOOK_PATH`. |
| `WEBHOOK_SECRET` | در حالت webhook **بله** | `change_this_secret` | مقدار `X-Telegram-Bot-Api-Secret-Token`؛ درخواست‌های بدون این هدر رد می‌شوند. یک رشتهٔ تصادفی بلند بگذارید. |

## زیرساخت (دیتابیس و Redis)

| متغیر | الزامی؟ | پیش‌فرض | توضیح |
|---|---|---|---|
| `DATABASE_URL` | **بله** | `postgresql+asyncpg://uploader:uploader_password@db:5432/uploader_bot` | رشتهٔ اتصال SQLAlchemy به PostgreSQL. باید با `POSTGRES_*` هم‌خوان باشد. |
| `REDIS_URL` | **بله** | `redis://redis:6379/0` | اتصال Redis (وضعیت FSM، صف حذف خودکار، بافر آلبوم). |
| `POSTGRES_USER` | **بله** | `uploader` | نام کاربری Postgres — کانتینر `db` با آن ساخته می‌شود و `update.sh`/پشتیبان‌گیری از آن استفاده می‌کنند. |
| `POSTGRES_PASSWORD` | **بله** | `uploader_password` | رمز Postgres. حتماً عوض کنید و با `DATABASE_URL` هماهنگ نگه دارید. |
| `POSTGRES_DB` | **بله** | `uploader_bot` | نام دیتابیس. |

## امنیت API و پنل

| متغیر | الزامی؟ | پیش‌فرض | توضیح |
|---|---|---|---|
| `API_KEY` | **بله** | `change_this_api_key` | کلید هدر `X-API-Key` برای endpoint های داخلی API. |
| `JWT_SECRET` | **بله** | `change_this_jwt_secret` | کلید امضای توکن‌های JWT در REST API نسخهٔ ۱ (`/api/v1`). محرمانه؛ لو رفتنش یعنی دسترسی کامل API. |
| `SESSION_SECRET` | **بله** | `change_this_session_secret` | کلید امضای کوکی نشست پنل وب. محرمانه. |
| `PANEL_PATH` | خیر | `/panel` | مسیر پایهٔ پنل وب. برای امنیت بیشتر می‌توانید مسیر غیرقابل حدس بگذارید. |

## درگاه پرداخت CentralPay

سایر درگاه‌ها (زرین‌پال، زیبال) از داخل پنل پیکربندی می‌شوند، نه `.env` — ببینید [gateways.md](gateways.md).

| متغیر | الزامی؟ | پیش‌فرض | توضیح |
|---|---|---|---|
| `CENTRALPAY_GETLINK_KEY` | خیر | خالی | کلید ساخت لینک پرداخت CentralPay. خالی = درگاه غیرفعال. |
| `CENTRALPAY_VERIFY_KEY` | خیر | خالی | کلید تأیید (verify) پرداخت CentralPay. درگاه فقط وقتی فعال است که **هر دو** کلید تنظیم شده باشند. |

## پیش‌فرض‌های رسانه و پلن

این‌ها فقط مقدار اولیه‌اند؛ مقدار مؤثر از داخل پنل/ربات قابل تغییر است.

| متغیر | الزامی؟ | پیش‌فرض | توضیح |
|---|---|---|---|
| `DEFAULT_PROTECT_CONTENT` | خیر | `false` | اگر `true` باشد فایل‌های تحویلی به‌صورت پیش‌فرض غیرقابل‌ذخیره/فوروارد ارسال می‌شوند. |
| `DEFAULT_AUTO_DELETE_SECONDS` | خیر | `0` | حذف خودکار پیام فایل پس از این تعداد ثانیه؛ `0` = خاموش. |
| `DEFAULT_PLAN` | خیر | `free` | پلن پیش‌فرض کاربران تازه‌وارد. |

## لایسنس و فعال‌سازی

جزئیات کامل در [licensing.md](licensing.md).

| متغیر | الزامی؟ | پیش‌فرض | توضیح |
|---|---|---|---|
| `LICENSE_DISABLED` | خیر | `true` | `true` = لایسنس کاملاً غیرفعال (پیش‌فرض؛ توسعه و نصب‌های موجود بدون تغییر کار می‌کنند). برای فعال‌سازی `false` بگذارید. |
| `LICENSE_KEY` | وقتی لایسنس فعال است | خالی | کلید لایسنس دریافتی از فروشنده. هرگز commit نکنید. |
| `LICENSE_SERVER_URL` | وقتی لایسنس فعال است | خالی | آدرس سرور فعال‌سازی (مثال: `https://license.example.com`). |
| `LICENSE_GRACE_DAYS` | خیر | `7` | مهلت آفلاین: اگر سرور فعال‌سازی در دسترس نباشد، نصبِ قبلاً تأییدشده تا این تعداد روز از آخرین تأیید موفق به کار ادامه می‌دهد. |
| `LICENSE_FILE` | خیر | `license.json` | مسیر فایل آینه (write-only) از وضعیت لایسنس؛ مرجع اصلی، دیتابیس است. |

## سرور فعال‌سازی مستقل (فروشنده)

فقط برای فروشنده؛ روی سرور مشتری لازم نیست. ببینید `activation_server/README.md`.

| متغیر | الزامی؟ | پیش‌فرض | توضیح |
|---|---|---|---|
| `ACTIVATION_DB` | خیر | `activation.db` | مسیر فایل SQLite سرور فعال‌سازی (کلیدها و seat ها). |

## چک‌لیست استقرار

- [ ] همهٔ مقادیر `change_this_*` با رشته‌های تصادفی بلند جایگزین شده‌اند (`openssl rand -hex 32`).
- [ ] `ADMIN_IDS` شناسهٔ عددی خود شما را دارد (از [@userinfobot](https://t.me/userinfobot)).
- [ ] `DATABASE_URL` با `POSTGRES_USER`/`POSTGRES_PASSWORD`/`POSTGRES_DB` هم‌خوان است.
- [ ] در حالت webhook: `DOMAIN` با HTTPS معتبر در دسترس است.
- [ ] فایل `.env` در گیت commit نشده است (در `.gitignore` است).
