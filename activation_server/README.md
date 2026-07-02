# Activation Server (اختیاری)

سرویس مستقل و کوچک فعال‌سازی لایسنس. **اپ اصلی بدون این سرویس هم کار می‌کند**
(حالت `LICENSE_DISABLED=true` یا دورهٔ مدارا/Grace).

## اجرا

```bash
docker compose -f activation_server/docker-compose.yml up -d
# یا بدون داکر:
ACTIVATION_DB=./activation.db uvicorn activation_server.main:app --port 8100
```

## صدور کلید (فروشنده)

```bash
python -m activation_server.issue --key CUST-ABC-123 --seats 2 --days 365
python -m activation_server.issue --list
python -m activation_server.issue --key CUST-ABC-123 --revoke
```

سپس در `.env` مشتری:

```
LICENSE_DISABLED=false
LICENSE_KEY=CUST-ABC-123
LICENSE_SERVER_URL=https://your-activation-host:8100
```

## پروتکل

- `POST /activate` `{key, fingerprint}` →
  `{ok, status, expires_at, allowed_install_count, seats_used}` —
  هر fingerprint یک «صندلی» مصرف می‌کند؛ فعال‌سازی مجدد همان fingerprint
  idempotent است؛ fingerprint جدید بعد از پر شدن سقف رد می‌شود (`seat_limit`).
- `POST /check` `{key, fingerprint}` → وضعیت فعلی
  (`active|expired|revoked|seat_limit|unknown`).
