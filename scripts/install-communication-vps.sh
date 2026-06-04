#!/usr/bin/env bash
set -euo pipefail

DRY_RUN=false
DOMAIN="${MATTERMOST_DOMAIN:-}"
LOCK_FILE="${LOCK_FILE:-versions.lock}"
INSTALL_DIR="${MATTERMOST_INSTALL_DIR:-/opt/ffc-ai-mattermost}"

usage() {
  printf 'usage: %s [--dry-run] --domain example.com\n' "$0"
}

log() {
  printf '[install-communication-vps] %s\n' "$*"
}

run() {
  if [ "$DRY_RUN" = true ]; then
    printf '[dry-run] %s\n' "$*"
  else
    "$@"
  fi
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=true ;;
    --domain) DOMAIN="$2"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage; exit 2 ;;
  esac
  shift
done

if [ -z "$DOMAIN" ]; then
  log 'domain is required for real TLS bootstrap; using dry-run placeholder'
  [ "$DRY_RUN" = true ] || exit 2
  DOMAIN='mattermost.example.invalid'
fi

read_lock() {
  grep "^$1=" "$LOCK_FILE" | cut -d= -f2-
}

MATTERMOST_IMAGE="$(read_lock mattermost_app_image)"
DB_IMAGE="$(read_lock mattermost_db_image)"
DOCKER_REF="$(read_lock mattermost_docker_ref)"

if [ -z "$MATTERMOST_IMAGE" ] || [ -z "$DB_IMAGE" ] || [ -z "$DOCKER_REF" ]; then
  log 'versions.lock must pin mattermost_app_image, mattermost_db_image, mattermost_docker_ref'
  exit 1
fi

log 'stage 01: detect VPS OS, CPU, memory, disk, public IP'
log "os=$(uname -s) arch=$(uname -m)"

log 'stage 02: install Docker Engine and Docker Compose plugin'
if ! command -v docker >/dev/null 2>&1; then
  log 'docker missing; install Docker Engine before platform_ready'
fi

log 'stage 03: configure domain and TLS'
log "domain=$DOMAIN"

log 'stage 04-07: create pinned Mattermost deployment'
run sudo mkdir -p "$INSTALL_DIR"/{config,data,logs,plugins,client/plugins,db}
if [ "$DRY_RUN" = false ]; then
  sudo tee "$INSTALL_DIR/docker-compose.yml" >/dev/null <<EOF
services:
  db:
    image: $DB_IMAGE
    restart: unless-stopped
    environment:
      POSTGRES_USER: mmuser
      POSTGRES_PASSWORD: \${MM_DB_PASSWORD:?set MM_DB_PASSWORD}
      POSTGRES_DB: mattermost
    volumes:
      - ./db:/var/lib/postgresql/data
  mattermost:
    image: $MATTERMOST_IMAGE
    restart: unless-stopped
    depends_on:
      - db
    ports:
      - "8065:8065"
    environment:
      MM_SQLSETTINGS_DRIVERNAME: postgres
      MM_SQLSETTINGS_DATASOURCE: postgres://mmuser:\${MM_DB_PASSWORD:?set MM_DB_PASSWORD}@db:5432/mattermost?sslmode=disable
      MM_SERVICESETTINGS_SITEURL: https://$DOMAIN
    volumes:
      - ./config:/mattermost/config
      - ./data:/mattermost/data
      - ./logs:/mattermost/logs
      - ./plugins:/mattermost/plugins
      - ./client/plugins:/mattermost/client/plugins
EOF
else
  log "would write $INSTALL_DIR/docker-compose.yml"
fi

log 'stage 08-12: create team, channels, bots, /ai command, status endpoint, shared secret'
log 'bootstrap uses mmctl --local when container is running; REST fallback is required by spec'

log 'stage 13-15: connect runner, run phone smoke tests, run backup smoke test'
log 'platform_ready=false until Mattermost stack is running and /ai loopback reaches runner'
