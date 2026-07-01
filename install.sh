#!/usr/bin/env bash
#
# ZedUploader — single-command Ubuntu deploy.
#
#   git clone https://github.com/Mhoseinshah1/zed-uploader && cd zed-uploader && sudo bash install.sh
#
# Flow: one command -> answer every question upfront -> then it runs fully
# unattended to completion (build, migrate, start, and in webhook mode also
# provision SSL and register the webhook). No prompts appear mid/after the run.
#
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

ENV_FILE=".env"
EXAMPLE_FILE=".env.example"

log()  { printf "\033[1;32m[install]\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m[warn]\033[0m %s\n" "$*"; }
die()  { printf "\033[1;31m[error]\033[0m %s\n" "$*" >&2; exit 1; }

# --- .env helpers (| delimiter; values are hex/urls, never contain |) --------
set_env() {
    local key="$1" value="$2"
    if grep -qE "^${key}=" "$ENV_FILE"; then
        sed -i "s|^${key}=.*|${key}=${value}|" "$ENV_FILE"
    else
        printf "%s=%s\n" "$key" "$value" >> "$ENV_FILE"
    fi
}

get_env() {
    local key="$1"
    grep -E "^${key}=" "$ENV_FILE" 2>/dev/null | head -n1 | cut -d= -f2- || true
}

# Ask for a required value; re-ask while blank. Shows current value as default,
# but treats known placeholders as "no default". Result -> the named variable.
ask_required() {
    local label="$1" default="$2" __outvar="$3" input show_default="$2"
    case "$default" in
        ""|put_bot_token_here|your_bot_username|123456789|https://example.com|change_this_*)
            show_default="" ;;
    esac
    while true; do
        if [ -n "$show_default" ]; then
            read -r -p "$label [$show_default]: " input || die "Input aborted (stdin closed)."
            input="${input:-$show_default}"
        else
            read -r -p "$label: " input || die "Input aborted (stdin closed)."
        fi
        [ -n "$input" ] && break
        warn "This value is required. Please enter a value."
    done
    printf -v "$__outvar" '%s' "$input"
}

# Generate a random secret only if the current value is unset/placeholder, so
# re-runs never rotate live secrets.
ensure_secret() {
    local key="$1" current
    current="$(get_env "$key")"
    case "$current" in
        ""|change_this_secret|change_this_api_key|change_this_jwt_secret|change_this_session_secret)
            set_env "$key" "$(openssl rand -hex 32)"
            log "Generated ${key}" ;;
        *)
            log "${key} already set — keeping" ;;
    esac
}

# Ask for a password twice with no echo; result -> the named variable.
ask_password() {
    local label="$1" __outvar="$2" p1 p2
    while true; do
        read -r -s -p "$label: " p1 || die "Input aborted (stdin closed)."; echo
        read -r -s -p "Confirm ${label}: " p2 || die "Input aborted (stdin closed)."; echo
        if [ -z "$p1" ]; then warn "Password cannot be empty."; continue; fi
        if [ "$p1" != "$p2" ]; then warn "Passwords do not match."; continue; fi
        break
    done
    printf -v "$__outvar" '%s' "$p1"
}

require_root() {
    if [ "$(id -u)" -ne 0 ]; then
        die "Please run as root (sudo bash install.sh)."
    fi
}

# ===========================================================================
# 0. Preconditions + a working .env to read defaults from (no apt/docker yet)
# ===========================================================================
require_root

if [ ! -f "$ENV_FILE" ]; then
    [ -f "$EXAMPLE_FILE" ] || die "$EXAMPLE_FILE not found — run from the repo root."
    cp "$EXAMPLE_FILE" "$ENV_FILE"
    log "Created $ENV_FILE from $EXAMPLE_FILE"
fi

# ===========================================================================
# 1. Collect ALL input upfront (the only interactive part of the script)
# ===========================================================================
echo
log "ZedUploader setup — please answer the following (everything is asked now)."
echo

ask_required "Telegram BOT_TOKEN"                 "$(get_env BOT_TOKEN)"    IN_BOT_TOKEN
ask_required "Bot username (with or without @)"   "$(get_env BOT_USERNAME)" IN_BOT_USERNAME
IN_BOT_USERNAME="${IN_BOT_USERNAME#@}"            # strip a single leading @
ask_required "Admin Telegram IDs (comma-separated)" "$(get_env ADMIN_IDS)"  IN_ADMIN_IDS

CUR_MODE="$(get_env BOT_MODE)"
DEFAULT_CHOICE=1
[ "$CUR_MODE" = "webhook" ] && DEFAULT_CHOICE=2
echo
echo "Choose bot mode:"
echo "  1) polling  — works immediately, no domain/SSL needed (good for testing)"
echo "  2) webhook  — production, requires a domain with DNS pointing here"
read -r -p "Mode [1/2] (default ${DEFAULT_CHOICE}): " MODE_CHOICE || true
MODE_CHOICE="${MODE_CHOICE:-$DEFAULT_CHOICE}"
if [ "$MODE_CHOICE" = "2" ]; then IN_BOT_MODE="webhook"; else IN_BOT_MODE="polling"; fi

IN_DOMAIN=""
IN_LE_EMAIL=""
if [ "$IN_BOT_MODE" = "webhook" ]; then
    echo
    ask_required "Public domain (with or without https://)" "$(get_env DOMAIN)" IN_DOMAIN
    # Normalize to a clean https:// URL with no trailing slash.
    IN_DOMAIN="${IN_DOMAIN#http://}"
    IN_DOMAIN="${IN_DOMAIN#https://}"
    IN_DOMAIN="${IN_DOMAIN%/}"
    IN_DOMAIN="https://${IN_DOMAIN}"
    ask_required "Email for Let's Encrypt (renewal notices)" "$(get_env LE_EMAIL)" IN_LE_EMAIL
fi

echo
log "Web panel login (used at <domain>/panel):"
ask_required "Panel admin username" "" IN_PANEL_USER
ask_password "Panel admin password" IN_PANEL_PASS

echo
log "All answers collected. The rest runs unattended — no more questions."

# ===========================================================================
# 2. Persist answers + secrets to .env
# ===========================================================================
set_env BOT_TOKEN    "$IN_BOT_TOKEN"
set_env BOT_USERNAME "$IN_BOT_USERNAME"
set_env ADMIN_IDS    "$IN_ADMIN_IDS"
set_env BOT_MODE     "$IN_BOT_MODE"
if [ "$IN_BOT_MODE" = "webhook" ]; then
    set_env DOMAIN   "$IN_DOMAIN"
    set_env LE_EMAIL "$IN_LE_EMAIL"
fi

ensure_secret WEBHOOK_SECRET
ensure_secret API_KEY
ensure_secret JWT_SECRET
ensure_secret SESSION_SECRET

# DB password: generate + sync DATABASE_URL only if still the default, so a
# re-run never breaks an already-initialized postgres volume.
CUR_PG_PASS="$(get_env POSTGRES_PASSWORD)"
if [ -z "$CUR_PG_PASS" ] || [ "$CUR_PG_PASS" = "uploader_password" ]; then
    DB_PASSWORD="$(openssl rand -hex 24)"
    PG_USER="$(get_env POSTGRES_USER)"; PG_USER="${PG_USER:-uploader}"
    PG_DB="$(get_env POSTGRES_DB)";     PG_DB="${PG_DB:-uploader_bot}"
    set_env POSTGRES_PASSWORD "$DB_PASSWORD"
    set_env DATABASE_URL "postgresql+asyncpg://${PG_USER}:${DB_PASSWORD}@db:5432/${PG_DB}"
    log "Generated database password (DATABASE_URL kept in sync)"
else
    log "Database password already set — keeping (DATABASE_URL unchanged)"
fi

# ===========================================================================
# 3. Unattended: apt deps, Docker, build, db+redis, migrate, start
# ===========================================================================
log "Installing base packages..."
apt-get update -y
apt-get install -y --no-install-recommends curl git ca-certificates openssl

if ! command -v docker >/dev/null 2>&1; then
    log "Installing Docker via get.docker.com ..."
    curl -fsSL https://get.docker.com | sh
else
    log "Docker already installed."
fi
docker compose version >/dev/null 2>&1 || die "'docker compose' plugin missing. Install Docker Compose v2."

log "Building images..."
docker compose build

log "Starting db + redis and waiting for health..."
docker compose up -d db redis
for _ in $(seq 1 30); do
    if docker compose ps --format '{{.Service}} {{.Health}}' 2>/dev/null | grep -q "db healthy" \
       && docker compose ps --format '{{.Service}} {{.Health}}' 2>/dev/null | grep -q "redis healthy"; then
        break
    fi
    sleep 2
done

log "Running database migrations..."
docker compose run --rm api alembic upgrade head

log "Creating the web panel user..."
docker compose run --rm api python -m app.panel.create_user \
    --username "$IN_PANEL_USER" --password "$IN_PANEL_PASS"

log "Starting all services..."
docker compose up -d

# ===========================================================================
# 4. Webhook mode only: provision nginx + TLS, then register the webhook
# ===========================================================================
if [ "$IN_BOT_MODE" = "webhook" ]; then
    WEBHOOK_PATH="$(get_env WEBHOOK_PATH)"; WEBHOOK_PATH="${WEBHOOK_PATH:-/telegram/webhook}"
    WEBHOOK_SECRET="$(get_env WEBHOOK_SECRET)"
    HOST=$(echo "$IN_DOMAIN" | sed -E 's~^https?://~~; s~/.*$~~')   # bare domain
    WEBHOOK_URL="${IN_DOMAIN}${WEBHOOK_PATH}"

    log "Provisioning nginx + certbot for ${HOST} ..."
    apt-get install -y nginx certbot python3-certbot-nginx
    mkdir -p /var/www/certbot

    sed "s/server_name .*/server_name ${HOST};/" nginx/uploader-bot.conf \
        > /etc/nginx/sites-available/zeduploader.conf
    ln -sf /etc/nginx/sites-available/zeduploader.conf /etc/nginx/sites-enabled/zeduploader.conf
    rm -f /etc/nginx/sites-enabled/default
    nginx -t && systemctl reload nginx || warn "nginx config test/reload failed — check the config."

    CERT_OK=0
    if certbot --nginx -d "${HOST}" --non-interactive --agree-tos -m "${IN_LE_EMAIL}" --redirect; then
        systemctl reload nginx || true
        CERT_OK=1
        log "TLS ready for ${HOST}"
    else
        warn "certbot could not obtain a certificate for ${HOST}."
        warn "Most likely DNS for ${HOST} isn't pointing here yet, or ports 80/443 are closed."
        warn "The containers are still running — nothing was rolled back."
    fi

    if [ "$CERT_OK" -eq 1 ]; then
        log "Setting Telegram webhook -> ${WEBHOOK_URL}"
        curl -sS "https://api.telegram.org/bot${IN_BOT_TOKEN}/setWebhook" \
            --data-urlencode "url=${WEBHOOK_URL}" \
            --data-urlencode "secret_token=${WEBHOOK_SECRET}" \
            --data-urlencode 'allowed_updates=["message","callback_query"]' \
            && echo || warn "setWebhook request failed — retry it after fixing connectivity."
    else
        warn "Skipped setWebhook because TLS isn't ready. After fixing DNS/firewall, run:"
        warn "  sudo certbot --nginx -d ${HOST}"
        warn "  curl -sS \"https://api.telegram.org/bot<BOT_TOKEN>/setWebhook\" \\"
        warn "       --data-urlencode \"url=${WEBHOOK_URL}\" \\"
        warn "       --data-urlencode \"secret_token=<WEBHOOK_SECRET from .env>\""
    fi
fi

# ===========================================================================
# 5. Summary
# ===========================================================================
echo
log "===================== ZedUploader is ready ====================="
log "Mode:          ${IN_BOT_MODE}"
log "Bot:           https://t.me/${IN_BOT_USERNAME}"
if [ "$IN_BOT_MODE" = "webhook" ]; then
    log "Health URL:    ${IN_DOMAIN}/health"
    log "Panel:         ${IN_DOMAIN}/panel"
else
    log "Panel:         http://localhost:8000/panel (SSH-tunnel; api is 127.0.0.1-only)"
fi
log "Local health:  http://localhost:8000/health"
log "Logs:          docker compose logs -f"
log "================================================================"
