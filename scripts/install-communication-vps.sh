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

compose() {
  if sudo docker compose version >/dev/null 2>&1; then
    sudo docker compose "$@"
  else
    sudo docker-compose "$@"
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
MATTERMOST_VERSION="$(read_lock mattermost_version || true)"
DB_IMAGE="$(read_lock mattermost_db_image)"
CADDY_IMAGE="$(read_lock mattermost_caddy_image)"
DOCKER_REF="$(read_lock mattermost_docker_ref)"
ARCH="${AI_TEST_ARCH:-$(uname -m)}"
DEPLOY_MODE="docker"
MATTERMOST_TARBALL_ARCH="amd64"
if [ "$ARCH" = "aarch64" ] || [ "$ARCH" = "arm64" ]; then
  DEPLOY_MODE="native-arm64"
  MATTERMOST_TARBALL_ARCH="arm64"
fi
MATTERMOST_VERSION="${MATTERMOST_VERSION:-10.5.3}"

if [ -z "$MATTERMOST_IMAGE" ] || [ -z "$DB_IMAGE" ] || [ -z "$CADDY_IMAGE" ] || [ -z "$DOCKER_REF" ]; then
  log 'versions.lock must pin mattermost_app_image, mattermost_db_image, mattermost_caddy_image, mattermost_docker_ref'
  exit 1
fi
for image_ref in "$MATTERMOST_IMAGE" "$DB_IMAGE" "$CADDY_IMAGE"; do
  case "$image_ref" in
    *@sha256:*) ;;
    *) log 'Mattermost, database, and Caddy image refs must include @sha256 digests'; exit 1 ;;
  esac
done

log 'stage 01: detect VPS OS, CPU, memory, disk, public IP'
log "os=$(uname -s) arch=$ARCH deployment=$DEPLOY_MODE"

log 'stage 02: install Docker Engine and Docker Compose plugin'
if ! command -v docker >/dev/null 2>&1; then
  if command -v apt-get >/dev/null 2>&1; then
    run sudo apt-get update
    if [ "$DRY_RUN" = true ]; then
      run sudo apt-get install -y docker.io docker-compose-plugin
    elif ! sudo apt-get install -y docker.io docker-compose-plugin; then
      log 'docker-compose-plugin unavailable; falling back to docker-compose package'
      sudo apt-get install -y docker.io docker-compose
    fi
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

run sudo mkdir -p "$INSTALL_DIR"/{config,data,logs,plugins,client/plugins,db,caddy,mattermost}
if [ "$DRY_RUN" = false ]; then
  MM_DB_PASSWORD="$(secret_b64)"
  AI_BRIDGE_SHARED_SECRET="$(secret_b64)"
  sudo tee "$INSTALL_DIR/.env" >/dev/null <<EOF
MM_DB_PASSWORD=$MM_DB_PASSWORD
AI_BRIDGE_SHARED_SECRET=$AI_BRIDGE_SHARED_SECRET
MATTERMOST_DOMAIN=$DOMAIN
MATTERMOST_DEPLOY_MODE=$DEPLOY_MODE
EOF
  sudo chmod 0600 "$INSTALL_DIR/.env"
  if [ "$DEPLOY_MODE" = "docker" ]; then
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
    image: $CADDY_IMAGE
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
    sudo tee "$INSTALL_DIR/caddy/Caddyfile" >/dev/null <<EOF
$DOMAIN {
  reverse_proxy host.docker.internal:8065
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
    ports:
      - "127.0.0.1:5432:5432"
    volumes:
      - ./db:/var/lib/postgresql/data
  caddy:
    image: $CADDY_IMAGE
    restart: unless-stopped
    extra_hosts:
      - "host.docker.internal:host-gateway"
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./caddy/Caddyfile:/etc/caddy/Caddyfile:ro
      - ./caddy/data:/data
      - ./caddy/config:/config
EOF
    MATTERMOST_URL="https://releases.mattermost.com/$MATTERMOST_VERSION/mattermost-$MATTERMOST_VERSION-linux-$MATTERMOST_TARBALL_ARCH.tar.gz"
    curl -fsSL "$MATTERMOST_URL" -o "$INSTALL_DIR/mattermost.tar.gz"
    sudo rm -rf "$INSTALL_DIR/mattermost"
    sudo tar xzf "$INSTALL_DIR/mattermost.tar.gz" -C "$INSTALL_DIR"
    sudo mkdir -p "$INSTALL_DIR/mattermost/data" "$INSTALL_DIR/mattermost/logs" "$INSTALL_DIR/mattermost/plugins" "$INSTALL_DIR/mattermost/client/plugins"
    sudo tee /etc/systemd/system/ffc-ai-mattermost.service >/dev/null <<EOF
[Unit]
Description=FFC-AI Mattermost
After=docker.service network-online.target
Requires=docker.service

[Service]
Type=simple
WorkingDirectory=$INSTALL_DIR/mattermost
Environment=MM_SERVICESETTINGS_SITEURL=https://$DOMAIN
Environment=MM_SERVICESETTINGS_LISTENADDRESS=:8065
Environment=MM_SQLSETTINGS_DRIVERNAME=postgres
Environment=MM_SQLSETTINGS_DATASOURCE=postgres://mmuser:$MM_DB_PASSWORD@127.0.0.1:5432/mattermost?sslmode=disable&connect_timeout=10
Environment=MM_FILESETTINGS_DIRECTORY=$INSTALL_DIR/mattermost/data
Environment=MM_PLUGINSETTINGS_DIRECTORY=$INSTALL_DIR/mattermost/plugins
Environment=MM_PLUGINSETTINGS_CLIENTDIRECTORY=$INSTALL_DIR/mattermost/client/plugins
ExecStart=$INSTALL_DIR/mattermost/bin/mattermost
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
    sudo systemctl daemon-reload
  fi
else
  log "would generate MM_DB_PASSWORD, AI_BRIDGE_SHARED_SECRET, Caddyfile, and $INSTALL_DIR/docker-compose.yml"
  if [ "$DEPLOY_MODE" = "native-arm64" ]; then
    log "would install Mattermost $MATTERMOST_VERSION linux-$MATTERMOST_TARBALL_ARCH tarball and systemd service"
  fi
fi

log 'stage 08-12: create team, channels, bots, /ai command, status endpoint, shared secret'
if [ "$DRY_RUN" = false ]; then
  (cd "$INSTALL_DIR" && compose up -d)
  if [ "$DEPLOY_MODE" = "native-arm64" ]; then
    sudo systemctl enable --now ffc-ai-mattermost.service
  fi
  for _ in $(seq 1 60); do
    if curl -fsS http://localhost:8065/api/v4/system/ping >/dev/null 2>&1; then
      break
    fi
    sleep 5
  done
  log 'waiting for Mattermost to expose mmctl'
  for _ in $(seq 1 60); do
    if [ "$DEPLOY_MODE" = "native-arm64" ]; then
      if (cd "$INSTALL_DIR/mattermost" && bin/mmctl --local version >/dev/null 2>&1); then
        break
      fi
    elif (cd "$INSTALL_DIR" && compose exec -T mattermost mmctl version >/dev/null 2>&1); then
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
  "mattermost_version": "$MATTERMOST_VERSION",
  "mattermost_deploy_mode": "$DEPLOY_MODE",
  "mattermost_db_image": "$DB_IMAGE",
  "mattermost_caddy_image": "$CADDY_IMAGE",
  "mattermost_docker_ref": "$DOCKER_REF",
  "platform_ready": false,
  "platform_ready_status": "pending_pairing_and_integration_validation",
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
log 'platform_ready=false until scripts/validate-integration.sh passes after runner pairing'
