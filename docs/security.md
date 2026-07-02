# نکات امنیتی

- **اسرار فقط در `.env`** (توسط install.sh تولید می‌شوند) — هرگز commit نکنید.
- پنل: bcrypt، سشن Redis با کوکی امضاشده، CSRF همه‌جا، قفل ورود، audit log،
  هدرهای امنیتی (CSP/X-Frame-Options/nosniff).
- API: نسخهٔ v1 با JWT یا سشن پنل؛ endpointهای قدیمی فقط-خواندنی با
  `X-API-Key`؛ rate limit سراسری per-IP (fail-open با قطع Redis).
- پول: دفترکل کیف پول (`SUM(tx)==balance`)، verify درگاه‌ها idempotent +
  amount-check، خرید پلن اتمیک، استارز idempotent روی charge id.
- تحویل فایل فقط برای مدیای approved+active؛ گذرواژهٔ فایل bcrypt + قفل ۳ خطا.
- دسترسی: uvicorn فقط روی 127.0.0.1؛ ورود SSH را سخت کنید؛ به‌روزرسانی منظم.
- هش/کلید هیچ‌وقت در پاسخ API یا لاگ‌ها نمایش داده نمی‌شود.
