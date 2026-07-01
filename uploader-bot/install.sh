#!/usr/bin/env bash
#
# ZedUploader — single-command Ubuntu deploy.
#
#   git clone REPO && cd uploader-bot && sudo bash install.sh
#
set -euo pipefail

ENV_FILE=".env"
EXAMPLE_FILE=".env.example"

log()  { printf "\033[1;32m[install]\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m[warn]\033[0m %s\n" "$*"; }
die()  { printf "\033[1;31m[error]\033[0m %s\n" "$*" >&2; exit 1; }

# --- sed helper: set KEY=VALUE in .env (| delimiter; values are hex/urls) ----
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
    grep -E "^${key}=" "$ENV_FILE" | head -n1 | cut -d= -f2-
}

# Prompt keeping the current value if the user just presses Enter.
prompt_env() {
    local key="$1" label="$2" current input
    current="$(get_env "$key" || true)"
    read -r -p "$label [$current]: " input || true
    if [ -n "$input" ]; then
        set_env "$key" "$input"
    fi
}

require_root() {
    if [ "$(id -u)" -ne 0 ]; then
        die "Please run as root (sudo bash install.sh)."
    fi
}

# ---------------------------------------------------------------------------
require_root

log "Updating apt and installing base packages..."
apt-get update -y
apt-get install -y --no-install-recommends curl git ca-certificates openssl

if ! command -v docker >/dev/null 2>&1; then
    log "Installing Docker via get.docker.com ..."
    curl -fsSL https://get.docker.com | sh
else
    log "Docker already installed."
fi

if ! docker compose version >/dev/null 2>&1; then
    die "'docker compose' plugin not available. Install Docker Compose v2 and re-run."
fi
log "docker compose OK: $(docker compose version | head -n1)"

# --- .env -------------------------------------------------------------------
if [ ! -f "$ENV_FILE" ]; then
    log "Creating $ENV_FILE from $EXAMPLE_FILE"
    cp "$EXAMPLE_FILE" "$ENV_FILE"
else
    log "$ENV_FILE already exists — keeping it (press Enter to keep current values)."
fi

log "Configure the required values:"
prompt_env BOT_TOKEN    "Telegram BOT_TOKEN"
prompt_env BOT_USERNAME "Bot username (without @)"
prompt_env ADMIN_IDS    "Admin Telegram IDs (comma separated)"
prompt_env DOMAIN       "Public domain (https://your.domain)"

# --- generate secrets (hex => URL-safe + sed-safe) --------------------------
log "Generating secrets..."
set_env WEBHOOK_SECRET "$(openssl rand -hex 32)"
set_env API_KEY        "$(openssl rand -hex 32)"
set_env JWT_SECRET     "$(openssl rand -hex 32)"

DB_PASSWORD="$(openssl rand -hex 24)"
PG_USER="$(get_env POSTGRES_USER)"; PG_USER="${PG_USER:-uploader}"
PG_DB="$(get_env POSTGRES_DB)";     PG_DB="${PG_DB:-uploader_bot}"
set_env POSTGRES_PASSWORD "$DB_PASSWORD"
# Keep DATABASE_URL in sync with the freshly generated DB password.
set_env DATABASE_URL "postgresql+asyncpg://${PG_USER}:${DB_PASSWORD}@db:5432/${PG_DB}"

# --- build & migrate --------------------------------------------------------
log "Building images..."
docker compose build

log "Starting db + redis and waiting for health..."
docker compose up -d db redis
for i in $(seq 1 30); do
    if docker compose ps --format '{{.Service}} {{.Health}}' 2>/dev/null | grep -q "db healthy" \
       && docker compose ps --format '{{.Service}} {{.Health}}' 2>/dev/null | grep -q "redis healthy"; then
        break
    fi
    sleep 2
done

log "Running database migrations..."
docker compose run --rm api alembic upgrade head

log "Starting all services..."
docker compose up -d

# --- set telegram webhook ---------------------------------------------------
BOT_TOKEN="$(get_env BOT_TOKEN)"
DOMAIN="$(get_env DOMAIN)"
WEBHOOK_PATH="$(get_env WEBHOOK_PATH)"; WEBHOOK_PATH="${WEBHOOK_PATH:-/telegram/webhook}"
WEBHOOK_SECRET="$(get_env WEBHOOK_SECRET)"
BOT_MODE="$(get_env BOT_MODE)"; BOT_MODE="${BOT_MODE:-webhook}"

if [ "$BOT_MODE" = "webhook" ] && [ -n "$BOT_TOKEN" ] && [ "$BOT_TOKEN" != "put_bot_token_here" ]; then
    WEBHOOK_URL="${DOMAIN%/}${WEBHOOK_PATH}"
    log "Setting Telegram webhook -> ${WEBHOOK_URL}"
    curl -sS "https://api.telegram.org/bot${BOT_TOKEN}/setWebhook" \
        --data-urlencode "url=${WEBHOOK_URL}" \
        --data-urlencode "secret_token=${WEBHOOK_SECRET}" \
        --data-urlencode 'allowed_updates=["message","callback_query"]' \
        && echo
else
    warn "Skipping webhook setup (BOT_MODE=$BOT_MODE or BOT_TOKEN not set)."
fi

log "Done!"
log "API health:  ${DOMAIN%/}/health   (local: http://localhost:8000/health)"
log "Bot:         https://t.me/$(get_env BOT_USERNAME)"
log "View logs:   docker compose logs -f"
