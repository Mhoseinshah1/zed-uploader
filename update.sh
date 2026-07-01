#!/usr/bin/env bash
#
# ZedUploader — unattended, data-safe update.
#
#   cd ~/zed-uploader && git pull && sudo bash update.sh
#
# Pulls the latest code, rebuilds images, applies migrations, and recreates the
# services. Never touches the `pgdata` volume or `.env` (both are preserved), so
# it is safe to run repeatedly.
#
set -euo pipefail

log()  { printf "\033[1;32m[update]\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m[warn]\033[0m %s\n" "$*"; }
die()  { printf "\033[1;31m[error]\033[0m %s\n" "$*" >&2; exit 1; }

# Always operate from the repo root (this script's own directory).
cd "$(dirname "$(readlink -f "$0")")"

if [ "$(id -u)" -ne 0 ]; then
    die "Please run as root (sudo bash update.sh)."
fi

docker compose version >/dev/null 2>&1 || die "'docker compose' plugin missing."

log "Pulling latest code..."
git fetch --all --prune
if ! git pull --ff-only; then
    die "git pull failed (you have local changes). Commit or stash them, then re-run."
fi

log "Rebuilding images..."
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

log "Applying database migrations..."
docker compose run --rm api alembic upgrade head

log "Recreating services with the new images..."
docker compose up -d

log "Update complete."
docker compose ps
log "Logs: docker compose logs -f"
