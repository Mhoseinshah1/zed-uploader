# نصب production (تک‌دستوری)

پیش‌نیاز: سرور Ubuntu 20.04+، دامنه (برای webhook)، توکن ربات از @BotFather.

```bash
git clone https://github.com/Mhoseinshah1/zed-uploader && cd zed-uploader && sudo bash install.sh
```

`install.sh` همه‌چیز را می‌پرسد و می‌سازد: `.env` با اسرار تصادفی
(`WEBHOOK_SECRET`, `API_KEY`, `JWT_SECRET`, رمز دیتابیس)، ایمیج‌ها، مایگریشن‌ها،
و در حالت webhook: نصب nginx + گواهی Let's Encrypt + ثبت وب‌هوک.

> ⚠️ هرگز مستقیم `docker compose up` نزنید مگر `.env` واقعی ساخته شده باشد؛
> مقادیر پیش‌فرض `change_this_*` ناامن‌اند.

## به‌روزرسانی

```bash
cd ~/zed-uploader && sudo bash update.sh
```

قبل از هر تغییر پشتیبان می‌گیرد (`/backups/pre-update-*.sql`، نگه‌داری ۷ نسخه)،
نسخهٔ قدیم→جدید و changelog را چاپ می‌کند، و اگر مایگریشن شکست بخورد با
راهنمای بازگشت (rollback) متوقف می‌شود — استک نیمه‌به‌روز رها نمی‌شود.

## سرویس‌ها

| سرویس | نقش |
|---|---|
| api | FastAPI + وب‌هوک تلگرام + پنل وب |
| bot | polling یا ثبت وب‌هوک |
| worker | حذف خودکار، ارسال همگانی، آلبوم‌ها، پشتیبان‌گیری، انقضای پلن، چک لایسنس |
| db | PostgreSQL 16 (volume: `pgdata`) |
| redis | صف‌ها، سشن پنل، rate limit |
