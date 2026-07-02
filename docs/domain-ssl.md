# دامنه و SSL (nginx + certbot)

1. رکورد DNS نوع A دامنه را به IP سرور بدهید و منتظر انتشار بمانید
   (`dig +short your.domain` باید IP سرور را برگرداند).
2. کانفیگ nginx نمونه را کپی کنید و **حتماً `server_name` را به دامنهٔ خودتان
   تغییر دهید** — رایج‌ترین خطا همین است:

```bash
sudo cp nginx/uploader-bot.conf /etc/nginx/sites-available/uploader-bot.conf
sudo ln -s /etc/nginx/sites-available/uploader-bot.conf /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d your.domain
```

3. در `.env` مقدار `DOMAIN=https://your.domain` باشد و وب‌هوک دوباره ست شود
   (اجرای مجدد `install.sh` یا ری‌استارت سرویس bot).

نکته‌ها: پورت ۸۰/۴۴۳ باید باز باشد؛ گواهی certbot خودکار تمدید می‌شود؛
uvicorn فقط روی `127.0.0.1:8000` گوش می‌دهد و nginx جلوی آن است.
