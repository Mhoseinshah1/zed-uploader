#!/usr/bin/env bash
#
# ZedUploader — unattended, data-safe update.
#
#   cd ~/zed-uploader && sudo bash update.sh
#
# Takes a pre-update database backup, pulls the latest code, rebuilds images,
# applies migrations, recreates the services, and health-checks the API. Never
# touches the `pgdata` volume or `.env` (both are preserved), so it is safe to
# run repeatedly. If migrations fail it STOPS and points at the fresh backup
# instead of leaving a half-updated stack.
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
[ -f .env ] || die ".env not found — copy .env.example to .env first."

# --- read DB credentials from .env (used for the backup + restore hint) -----
read_env() {
    # last matching KEY=VALUE wins; strip optional surrounding quotes
    local val
    val=$(grep -E "^$1=" .env | tail -n1 | cut -d= -f2-)
    val=${val%\"}; val=${val#\"}; val=${val%\'}; val=${val#\'}
    printf '%s' "$val"
}
POSTGRES_USER=$(read_env POSTGRES_USER)
POSTGRES_DB=$(read_env POSTGRES_DB)
[ -n "$POSTGRES_USER" ] && [ -n "$POSTGRES_DB" ] \
    || die "POSTGRES_USER / POSTGRES_DB missing from .env — cannot back up safely."

BACKUP_DIR=/backups
KEEP_BACKUPS=7

wait_healthy() {
    local svc="$1" i
    for i in $(seq 1 30); do
        if docker compose ps --format '{{.Service}} {{.Health}}' 2>/dev/null \
           | grep -q "$svc healthy"; then
            return 0
        fi
        sleep 2
    done
    return 1
}

OLD_COMMIT=$(git rev-parse HEAD)

# --- 1) back up the database BEFORE pulling ---------------------------------
log "Ensuring the database is up for a pre-update backup..."
docker compose up -d db
wait_healthy db || die "Database did not become healthy; aborting before any change."

mkdir -p "$BACKUP_DIR"
TS=$(date -u +%Y%m%d-%H%M%S)
BACKUP_FILE="$BACKUP_DIR/pre-update-$TS.sql"
log "Backing up database to $BACKUP_FILE ..."
if ! docker compose exec -T db pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" > "$BACKUP_FILE"; then
    rm -f "$BACKUP_FILE"
    die "Backup failed; aborting update (nothing was changed)."
fi
log "Backup OK ($(du -h "$BACKUP_FILE" | cut -f1))."

# keep only the most recent $KEEP_BACKUPS backups
find "$BACKUP_DIR" -maxdepth 1 -type f -name 'pre-update-*.sql' -printf '%T@\t%p\n' \
    | sort -rn | awk -v keep="$KEEP_BACKUPS" 'NR>keep {print $2}' \
    | xargs -r rm -f

# --- 2) pull the latest code ------------------------------------------------
log "Pulling latest code..."
git fetch --all --prune
if ! git pull --ff-only; then
    die "git pull failed (you have local changes). Commit or stash them, then re-run."
fi
NEW_COMMIT=$(git rev-parse HEAD)

# --- 3) build + start infra -------------------------------------------------
log "Rebuilding images..."
docker compose build

log "Starting db + redis and waiting for health..."
docker compose up -d db redis
wait_healthy db   || die "db did not become healthy."
wait_healthy redis || die "redis did not become healthy."

# --- 4) migrate (STOP on failure; point at the backup) ----------------------
log "Applying database migrations..."
if ! docker compose run --rm api alembic upgrade head; then
    die "Migration FAILED — the stack was NOT fully updated (services keep the previous version).
     Your pre-update backup is at: $BACKUP_FILE
     To restore it:
       cat '$BACKUP_FILE' | docker compose exec -T db psql -U '$POSTGRES_USER' '$POSTGRES_DB'
     Fix the migration, then re-run update.sh."
fi

# --- 5) recreate services ---------------------------------------------------
log "Recreating services with the new images..."
docker compose up -d

# --- 6) health check --------------------------------------------------------
log "Health check (http://127.0.0.1:8000/health)..."
healthy=false
for _ in $(seq 1 15); do
    if curl -fsS http://127.0.0.1:8000/health >/dev/null 2>&1; then
        healthy=true
        break
    fi
    sleep 2
done
if $healthy; then
    log "Health check OK."
else
    warn "Health check FAILED — inspect logs: docker compose logs -f api"
fi

# --- 7) changelog -----------------------------------------------------------
if [ "$OLD_COMMIT" != "$NEW_COMMIT" ]; then
    log "Changes ${OLD_COMMIT:0:8}..${NEW_COMMIT:0:8}:"
    git log --oneline "$OLD_COMMIT..$NEW_COMMIT" || true
else
    log "Already at the latest commit; no code changes."
fi

log "Update complete."
docker compose ps
log "Logs: docker compose logs -f"
