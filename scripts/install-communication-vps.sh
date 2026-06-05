#!/usr/bin/env bash
set -euo pipefail

DRY_RUN=false
DOMAIN="${MATTERMOST_DOMAIN:-}"
INSTALL_DIR="${MATTERMOST_INSTALL_DIR:-/opt/ffc-ai-mattermost}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LOCK_FILE="$REPO_ROOT/versions.lock"
BOOTSTRAP_MATTERMOST_SCRIPT="${BOOTSTRAP_MATTERMOST_SCRIPT:-$SCRIPT_DIR/bootstrap-mattermost.sh}"

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
  grep "^$1=" "$LOCK_FILE" 2>/dev/null | cut -d= -f2- || true
}

env_file_value() {
  local key="$1"
  [ "$DRY_RUN" = false ] && [ -f "$INSTALL_DIR/.env" ] || return 0
  sudo awk -F= -v key="$key" '$1 == key {print substr($0, index($0, "=") + 1); exit}' "$INSTALL_DIR/.env"
}

strip_v_prefix() {
  printf '%s' "$1" | sed 's/^v//'
}

latest_mattermost_version() {
  curl -fsSL "https://api.github.com/repos/mattermost/mattermost/releases/latest" |
    python3 -c 'import json,sys
tag = json.load(sys.stdin).get("tag_name", "")
tag = tag[1:] if tag.startswith("v") else tag
if not tag:
    raise SystemExit("Mattermost latest release did not include tag_name")
print(tag)'
}

version_ge() {
  python3 - "$1" "$2" <<'PY'
import sys

def parts(value: str) -> tuple[int, int, int]:
    raw = value.lstrip("v").split("-", 1)[0].split(".")
    nums = [int(part) for part in raw[:3]]
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums)

raise SystemExit(0 if parts(sys.argv[1]) >= parts(sys.argv[2]) else 1)
PY
}

mattermost_download_url() {
  local version="$1"
  local arch="$2"
  local team_url="https://releases.mattermost.com/$version/mattermost-team-$version-linux-$arch.tar.gz"
  local fallback_url="https://releases.mattermost.com/$version/mattermost-$version-linux-$arch.tar.gz"
  if curl -fsI "$team_url" >/dev/null 2>&1; then
    printf '%s\n' "$team_url"
    return
  fi
  if curl -fsI "$fallback_url" >/dev/null 2>&1; then
    printf '%s\n' "$fallback_url"
    return
  fi
  log "unable to find Mattermost linux-$arch tarball for version $version"
  exit 1
}

if [ -n "${MATTERMOST_IMAGE_REPOSITORY:-}" ]; then
  log 'MATTERMOST_IMAGE_REPOSITORY override is not supported; this installer uses the repository pinned in versions.lock'
  exit 2
fi
if [ -n "${MATTERMOST_IMAGE:-}" ]; then
  log 'MATTERMOST_IMAGE override is not supported because it can bypass the minimum Mattermost version guard; set MATTERMOST_IMAGE_REPOSITORY and MATTERMOST_VERSION instead'
  exit 2
fi
MATTERMOST_HARD_MIN_VERSION="10.11.0"
MATTERMOST_LOCK_MIN_VERSION="$(read_lock mattermost_min_version)"
MATTERMOST_REQUESTED_MIN_VERSION="${MATTERMOST_MIN_VERSION:-$MATTERMOST_LOCK_MIN_VERSION}"
MATTERMOST_MIN_VERSION="${MATTERMOST_REQUESTED_MIN_VERSION:-$MATTERMOST_HARD_MIN_VERSION}"
if ! version_ge "$MATTERMOST_MIN_VERSION" "$MATTERMOST_HARD_MIN_VERSION"; then
  MATTERMOST_MIN_VERSION="$MATTERMOST_HARD_MIN_VERSION"
fi
MATTERMOST_VERSION_REQUEST="${MATTERMOST_VERSION:-$(read_lock mattermost_version)}"
MATTERMOST_VERSION_REQUEST="${MATTERMOST_VERSION_REQUEST:-latest}"
if [ "$MATTERMOST_VERSION_REQUEST" = latest ] || [ "$MATTERMOST_VERSION_REQUEST" = auto ]; then
  MATTERMOST_VERSION="$(latest_mattermost_version)"
  MATTERMOST_VERSION_SOURCE="${MATTERMOST_VERSION_SOURCE:-github_latest}"
else
  MATTERMOST_VERSION="$(strip_v_prefix "$MATTERMOST_VERSION_REQUEST")"
  MATTERMOST_VERSION_SOURCE="${MATTERMOST_VERSION_SOURCE:-explicit}"
fi
if ! version_ge "$MATTERMOST_VERSION" "$MATTERMOST_MIN_VERSION"; then
  log "Mattermost $MATTERMOST_VERSION is below required minimum $MATTERMOST_MIN_VERSION for current mobile clients"
  exit 1
fi
MATTERMOST_IMAGE_REPOSITORY="$(read_lock mattermost_image_repository)"
MATTERMOST_IMAGE_REPOSITORY="${MATTERMOST_IMAGE_REPOSITORY:-mattermost/mattermost-team-edition}"
MATTERMOST_IMAGE="$MATTERMOST_IMAGE_REPOSITORY:$MATTERMOST_VERSION"
DB_IMAGE="$(read_lock mattermost_db_image)"
CADDY_IMAGE="$(read_lock mattermost_caddy_image)"
DOCKER_REF="$(read_lock mattermost_docker_ref)"
MATTERMOST_CONTAINER_UID="${MATTERMOST_CONTAINER_UID:-2000}"
MATTERMOST_CONTAINER_GID="${MATTERMOST_CONTAINER_GID:-2000}"
ARCH="${AI_TEST_ARCH:-$(uname -m)}"
DEPLOY_MODE="docker"
MATTERMOST_TARBALL_ARCH="amd64"
if [ "$ARCH" = "aarch64" ] || [ "$ARCH" = "arm64" ]; then
  DEPLOY_MODE="native-arm64"
  MATTERMOST_TARBALL_ARCH="arm64"
fi

if [ -z "$MATTERMOST_IMAGE" ] || [ -z "$DB_IMAGE" ] || [ -z "$CADDY_IMAGE" ] || [ -z "$DOCKER_REF" ]; then
  log 'versions.lock must define mattermost_db_image, mattermost_caddy_image, and mattermost_docker_ref'
  exit 1
fi
for image_ref in "$DB_IMAGE" "$CADDY_IMAGE"; do
  case "$image_ref" in
    *@sha256:*) ;;
    *) log 'database and Caddy image refs must include @sha256 digests'; exit 1 ;;
  esac
done

log 'stage 01: detect VPS OS, CPU, memory, disk, public IP'
log "os=$(uname -s) arch=$ARCH deployment=$DEPLOY_MODE"
log "mattermost_version=$MATTERMOST_VERSION source=$MATTERMOST_VERSION_SOURCE minimum=$MATTERMOST_MIN_VERSION image=$MATTERMOST_IMAGE"

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

copy_native_dir_if_empty() {
  local source="$1"
  local target="$2"
  [ -d "$source" ] || return 0
  sudo mkdir -p "$target"
  if [ -z "$(sudo find "$target" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null || true)" ]; then
    sudo cp -a "$source/." "$target/"
  fi
}

run sudo mkdir -p "$INSTALL_DIR"/{config,data,logs,plugins,client/plugins,db,caddy,mattermost}
if [ "$DEPLOY_MODE" = "docker" ]; then
  run sudo chown -R "$MATTERMOST_CONTAINER_UID:$MATTERMOST_CONTAINER_GID" \
    "$INSTALL_DIR/config" "$INSTALL_DIR/data" "$INSTALL_DIR/logs" "$INSTALL_DIR/plugins" "$INSTALL_DIR/client/plugins"
  run sudo chmod -R u+rwX,g+rwX \
    "$INSTALL_DIR/config" "$INSTALL_DIR/data" "$INSTALL_DIR/logs" "$INSTALL_DIR/plugins" "$INSTALL_DIR/client/plugins"
fi
if [ "$DRY_RUN" = false ]; then
  MM_DB_PASSWORD="${MM_DB_PASSWORD:-$(env_file_value MM_DB_PASSWORD)}"
  AI_BRIDGE_SHARED_SECRET="${AI_BRIDGE_SHARED_SECRET:-$(env_file_value AI_BRIDGE_SHARED_SECRET)}"
  MATTERMOST_ADMIN_USERNAME="${MATTERMOST_ADMIN_USERNAME:-$(env_file_value MATTERMOST_ADMIN_USERNAME)}"
  MATTERMOST_ADMIN_EMAIL="${MATTERMOST_ADMIN_EMAIL:-$(env_file_value MATTERMOST_ADMIN_EMAIL)}"
  MATTERMOST_ADMIN_PASSWORD="${MATTERMOST_ADMIN_PASSWORD:-$(env_file_value MATTERMOST_ADMIN_PASSWORD)}"
  MM_DB_PASSWORD="${MM_DB_PASSWORD:-$(secret_b64)}"
  AI_BRIDGE_SHARED_SECRET="${AI_BRIDGE_SHARED_SECRET:-$(secret_b64)}"
  MATTERMOST_ADMIN_USERNAME="${MATTERMOST_ADMIN_USERNAME:-ai-admin}"
  MATTERMOST_ADMIN_EMAIL="${MATTERMOST_ADMIN_EMAIL:-admin@$DOMAIN}"
  MATTERMOST_ADMIN_PASSWORD="${MATTERMOST_ADMIN_PASSWORD:-$(secret_b64)}"
  TMP_ENV="$(mktemp)"
  cat > "$TMP_ENV" <<EOF
MM_DB_PASSWORD=$MM_DB_PASSWORD
AI_BRIDGE_SHARED_SECRET=$AI_BRIDGE_SHARED_SECRET
MATTERMOST_DOMAIN=$DOMAIN
MATTERMOST_DEPLOY_MODE=$DEPLOY_MODE
MATTERMOST_ADMIN_USERNAME=$MATTERMOST_ADMIN_USERNAME
MATTERMOST_ADMIN_EMAIL=$MATTERMOST_ADMIN_EMAIL
MATTERMOST_ADMIN_PASSWORD=$MATTERMOST_ADMIN_PASSWORD
EOF
  if [ -f "$INSTALL_DIR/.env" ]; then
    sudo awk -F= '
      $1 != "MM_DB_PASSWORD" &&
      $1 != "AI_BRIDGE_SHARED_SECRET" &&
      $1 != "MATTERMOST_DOMAIN" &&
      $1 != "MATTERMOST_DEPLOY_MODE" &&
      $1 != "MATTERMOST_ADMIN_USERNAME" &&
      $1 != "MATTERMOST_ADMIN_EMAIL" &&
      $1 != "MATTERMOST_ADMIN_PASSWORD"
    ' "$INSTALL_DIR/.env" >> "$TMP_ENV"
  fi
  sudo cp "$TMP_ENV" "$INSTALL_DIR/.env"
  rm -f "$TMP_ENV"
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
      MM_SERVICESETTINGS_ENABLELOCALMODE: "true"
      MM_SERVICESETTINGS_LOCALMODESOCKETLOCATION: /var/tmp/mattermost_local.socket
      MM_SERVICESETTINGS_ENABLEBOTACCOUNTSCREATION: "true"
      MM_SERVICESETTINGS_ENABLEINCOMINGWEBHOOKS: "true"
      MM_SERVICESETTINGS_ENABLECOMMANDS: "true"
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
    MATTERMOST_DOWNLOAD_URL="$(mattermost_download_url "$MATTERMOST_VERSION" "$MATTERMOST_TARBALL_ARCH")"
    log "downloading Mattermost from $MATTERMOST_DOWNLOAD_URL"
    curl -fsSL "$MATTERMOST_DOWNLOAD_URL" -o "$INSTALL_DIR/mattermost.tar.gz"
    NATIVE_DATA_DIR="$INSTALL_DIR/native-data"
    sudo mkdir -p "$NATIVE_DATA_DIR/data" "$NATIVE_DATA_DIR/logs" "$NATIVE_DATA_DIR/plugins" "$NATIVE_DATA_DIR/client/plugins"
    copy_native_dir_if_empty "$INSTALL_DIR/mattermost/data" "$NATIVE_DATA_DIR/data"
    copy_native_dir_if_empty "$INSTALL_DIR/mattermost/logs" "$NATIVE_DATA_DIR/logs"
    copy_native_dir_if_empty "$INSTALL_DIR/mattermost/plugins" "$NATIVE_DATA_DIR/plugins"
    copy_native_dir_if_empty "$INSTALL_DIR/mattermost/client/plugins" "$NATIVE_DATA_DIR/client/plugins"
    sudo rm -rf "$INSTALL_DIR/mattermost"
    sudo tar xzf "$INSTALL_DIR/mattermost.tar.gz" -C "$INSTALL_DIR"
    sudo mkdir -p "$NATIVE_DATA_DIR/data" "$NATIVE_DATA_DIR/logs" "$NATIVE_DATA_DIR/plugins" "$NATIVE_DATA_DIR/client/plugins"
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
Environment=MM_SERVICESETTINGS_ENABLELOCALMODE=true
Environment=MM_SERVICESETTINGS_LOCALMODESOCKETLOCATION=/var/tmp/mattermost_local.socket
Environment=MM_SERVICESETTINGS_ENABLEBOTACCOUNTSCREATION=true
Environment=MM_SERVICESETTINGS_ENABLEINCOMINGWEBHOOKS=true
Environment=MM_SERVICESETTINGS_ENABLECOMMANDS=true
Environment=MM_SQLSETTINGS_DRIVERNAME=postgres
Environment=MM_SQLSETTINGS_DATASOURCE=postgres://mmuser:$MM_DB_PASSWORD@127.0.0.1:5432/mattermost?sslmode=disable&connect_timeout=10
Environment=MM_FILESETTINGS_DIRECTORY=$NATIVE_DATA_DIR/data
Environment=MM_LOGSETTINGS_FILELOCATION=$NATIVE_DATA_DIR/logs
Environment=MM_PLUGINSETTINGS_DIRECTORY=$NATIVE_DATA_DIR/plugins
Environment=MM_PLUGINSETTINGS_CLIENTDIRECTORY=$NATIVE_DATA_DIR/client/plugins
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
  MMCTL_READY=false
  for _ in $(seq 1 60); do
    if [ "$DEPLOY_MODE" = "native-arm64" ]; then
      if (cd "$INSTALL_DIR/mattermost" && bin/mmctl --local version >/dev/null 2>&1); then
        MMCTL_READY=true
        break
      fi
    elif (cd "$INSTALL_DIR" && compose exec -T mattermost mmctl version >/dev/null 2>&1); then
      MMCTL_READY=true
      break
    fi
    sleep 5
  done
  [ "$MMCTL_READY" = true ] || { log 'Mattermost mmctl local mode did not become ready'; exit 1; }
  MATTERMOST_INSTALL_DIR="$INSTALL_DIR" \
    MATTERMOST_URL="http://127.0.0.1:8065" \
    MATTERMOST_ADMIN_USERNAME="ai-admin" \
    MATTERMOST_ADMIN_EMAIL="admin@$DOMAIN" \
    MATTERMOST_ADMIN_PASSWORD="$MATTERMOST_ADMIN_PASSWORD" \
    "$BOOTSTRAP_MATTERMOST_SCRIPT"
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
  "mattermost_version_source": "$MATTERMOST_VERSION_SOURCE",
  "mattermost_min_version": "$MATTERMOST_MIN_VERSION",
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
