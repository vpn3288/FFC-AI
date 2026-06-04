#!/usr/bin/env bash
set -euo pipefail

DRY_RUN=false
DOMAIN="${MATTERMOST_DOMAIN:-}"
LOCK_FILE="${LOCK_FILE:-versions.lock}"
INSTALL_DIR="${MATTERMOST_INSTALL_DIR:-/opt/ffc-ai-mattermost}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

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
  if command -v apt-get >/dev/null 2>&1; then
    run sudo apt-get update
    run sudo apt-get install -y docker.io docker-compose-plugin
    run sudo systemctl enable --now docker
  else
    log 'docker missing and apt-get unavailable; install Docker Engine before platform_ready'
  fi
fi

log 'stage 03: configure domain and TLS'
log "domain=$DOMAIN"

log 'stage 04-07: create pinned Mattermost deployment'
secret_b64() {
  python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(32))
PY
}

run sudo mkdir -p "$INSTALL_DIR"/{config,data,logs,plugins,client/plugins,db,caddy}
if [ "$DRY_RUN" = false ]; then
  MM_DB_PASSWORD="$(secret_b64)"
  AI_BRIDGE_SHARED_SECRET="$(secret_b64)"
  sudo tee "$INSTALL_DIR/.env" >/dev/null <<EOF
MM_DB_PASSWORD=$MM_DB_PASSWORD
AI_BRIDGE_SHARED_SECRET=$AI_BRIDGE_SHARED_SECRET
MATTERMOST_DOMAIN=$DOMAIN
EOF
  sudo chmod 0600 "$INSTALL_DIR/.env"
  sudo tee "$INSTALL_DIR/caddy/Caddyfile" >/dev/null <<EOF
$DOMAIN {
  reverse_proxy mattermost:8065
}
EOF
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
  caddy:
    image: caddy:2.8.4-alpine
    restart: unless-stopped
    depends_on:
      - mattermost
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./caddy/Caddyfile:/etc/caddy/Caddyfile:ro
      - ./caddy/data:/data
      - ./caddy/config:/config
EOF
else
  log "would generate MM_DB_PASSWORD, AI_BRIDGE_SHARED_SECRET, Caddyfile, and $INSTALL_DIR/docker-compose.yml"
fi

log 'stage 08-12: create team, channels, bots, /ai command, status endpoint, shared secret'
if [ "$DRY_RUN" = false ]; then
  (cd "$INSTALL_DIR" && sudo docker compose up -d)
  for _ in $(seq 1 60); do
    if curl -fsS http://localhost:8065/api/v4/system/ping >/dev/null 2>&1; then
      break
    fi
    sleep 5
  done
  log 'waiting for Mattermost container to expose mmctl'
  for _ in $(seq 1 60); do
    if (cd "$INSTALL_DIR" && sudo docker compose exec -T mattermost mmctl version >/dev/null 2>&1); then
      break
    fi
    sleep 5
  done
  MATTERMOST_INSTALL_DIR="$INSTALL_DIR" "$SCRIPT_DIR/bootstrap-mattermost.sh"
else
  log 'would run docker compose up -d and bootstrap Mattermost objects with mmctl --local'
fi

log 'stage 13-15: connect runner, run phone smoke tests, run backup smoke test'
if [ "$DRY_RUN" = false ]; then
  sudo tee "$INSTALL_DIR/install-manifest.json" >/dev/null <<EOF
{
  "component": "mattermost-communication-platform",
  "domain": "$DOMAIN",
  "install_dir": "$INSTALL_DIR",
  "mattermost_app_image": "$MATTERMOST_IMAGE",
  "mattermost_db_image": "$DB_IMAGE",
  "mattermost_docker_ref": "$DOCKER_REF",
  "created_files": [
    "$INSTALL_DIR/.env",
    "$INSTALL_DIR/caddy/Caddyfile",
    "$INSTALL_DIR/docker-compose.yml",
    "$INSTALL_DIR/install-manifest.json"
  ],
  "required_objects": [
    "team:ai-lab",
    "channel:ai-ops",
    "channel:ai-status",
    "channel:ai-reviews",
    "channel:ai-errors",
    "channel:ai-archive",
    "slash-command:/ai",
    "bot:ai-bridge"
  ]
}
EOF
  sudo chmod 0600 "$INSTALL_DIR/install-manifest.json"
  log "bridge shared secret written to $INSTALL_DIR/.env with mode 0600"
  log 'transfer the secret to the runner through SSH, the credential broker, or another encrypted channel; it is not printed to stdout'
else
  log "would write $INSTALL_DIR/install-manifest.json"
fi
log 'platform_ready=false until Mattermost stack is running and /ai loopback reaches runner'
