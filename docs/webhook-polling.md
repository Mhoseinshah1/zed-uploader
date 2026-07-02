# Webhook در برابر Polling

| | Webhook | Polling |
|---|---|---|
| نیازمندی | دامنه + SSL معتبر | هیچ |
| مناسبِ | production | تست/توسعهٔ محلی |
| تنظیم | `BOT_MODE=webhook` + `DOMAIN` | `BOT_MODE=polling` |

در حالت webhook، سرویس bot فقط وب‌هوک را ثبت می‌کند و آپدیت‌ها به
`DOMAIN + WEBHOOK_PATH` (پیش‌فرض `/telegram/webhook`) می‌آیند و با
`WEBHOOK_SECRET` اعتبارسنجی می‌شوند. در حالت polling خود سرویس bot آپدیت
می‌گیرد و دامنه لازم نیست. تغییر حالت = ویرایش `.env` + `docker compose up -d`.
