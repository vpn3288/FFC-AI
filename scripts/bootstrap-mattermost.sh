#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="${MATTERMOST_INSTALL_DIR:-/opt/ffc-ai-mattermost}"
MANIFEST="$INSTALL_DIR/mattermost-objects.json"
MATTERMOST_URL="${MATTERMOST_URL:-}"
MATTERMOST_ADMIN_TOKEN="${MATTERMOST_ADMIN_TOKEN:-}"
MATTERMOST_ADMIN_USERNAME="${MATTERMOST_ADMIN_USERNAME:-}"
MATTERMOST_ADMIN_EMAIL="${MATTERMOST_ADMIN_EMAIL:-}"
MATTERMOST_ADMIN_PASSWORD="${MATTERMOST_ADMIN_PASSWORD:-}"
BRIDGE_COMMAND_URL="${BRIDGE_COMMAND_URL:-}"
WEBHOOK_CHANNEL_ID="${WEBHOOK_CHANNEL_ID:-}"
MATTERMOST_SYNC_ADMIN_PASSWORD="${MATTERMOST_SYNC_ADMIN_PASSWORD:-true}"
MATTERMOST_RESTART_AFTER_INTERNAL_ALLOW="${MATTERMOST_RESTART_AFTER_INTERNAL_ALLOW:-auto}"
MATTERMOST_RESTART_REQUIRED=false

log() {
  printf '[bootstrap-mattermost] %s\n' "$*"
}

env_file_value() {
  local key="$1"
  [ -f "$INSTALL_DIR/.env" ] || return 0
  awk -F= -v key="$key" '$1 == key {print substr($0, index($0, "=") + 1); exit}' "$INSTALL_DIR/.env"
}

MATTERMOST_URL="${MATTERMOST_URL:-http://127.0.0.1:8065}"
MATTERMOST_ADMIN_USERNAME="${MATTERMOST_ADMIN_USERNAME:-$(env_file_value MATTERMOST_ADMIN_USERNAME)}"
MATTERMOST_ADMIN_EMAIL="${MATTERMOST_ADMIN_EMAIL:-$(env_file_value MATTERMOST_ADMIN_EMAIL)}"
MATTERMOST_ADMIN_PASSWORD="${MATTERMOST_ADMIN_PASSWORD:-$(env_file_value MATTERMOST_ADMIN_PASSWORD)}"
MATTERMOST_ADMIN_USERNAME="${MATTERMOST_ADMIN_USERNAME:-ai-admin}"
MATTERMOST_ADMIN_EMAIL="${MATTERMOST_ADMIN_EMAIL:-admin@example.invalid}"

mmctl() {
  if [ -x "$INSTALL_DIR/mattermost/bin/mmctl" ]; then
    (cd "$INSTALL_DIR/mattermost" && bin/mmctl --local "$@")
  else
    (cd "$INSTALL_DIR" && compose exec -T mattermost mmctl --local "$@")
  fi
}

compose() {
  if sudo docker compose version >/dev/null 2>&1; then
    sudo docker compose "$@"
  else
    sudo docker-compose "$@"
  fi
}

require_mmctl_match() {
  local description="$1"
  local pattern="$2"
  shift 2
  if ! mmctl "$@" | grep -q "$pattern"; then
    log "missing Mattermost object after create attempt: $description"
    exit 1
  fi
}

ensure_team() {
  local name="$1"
  local display="$2"
  mmctl team list | grep -q "$name" || mmctl team create --name "$name" --display-name "$display"
  require_mmctl_match "team:$name" "$name" team list
}

ensure_channel() {
  local team="$1"
  local name="$2"
  local display="$3"
  mmctl channel list "$team" | grep -q "$name" || mmctl channel create --team "$team" --name "$name" --display-name "$display"
  require_mmctl_match "channel:$team/$name" "$name" channel list "$team"
}

ensure_bot() {
  local username="$1"
  local display="$2"
  if ! rest_json GET "$MATTERMOST_URL/api/v4/bots?per_page=200" | python3 -c 'import json,sys
target = sys.argv[1]
sys.exit(0 if any(item.get("username") == target for item in json.load(sys.stdin)) else 1)' "$username"
  then
    rest_json POST "$MATTERMOST_URL/api/v4/bots" "{\"username\":\"$username\",\"display_name\":\"$display\",\"description\":\"FFC-AI bot identity\"}" >/dev/null
  fi
}

rest_json() {
  local method="$1"
  local url="$2"
  local data="${3:-}"
  if [ -n "$data" ]; then
    curl -fsS -X "$method" "$url" \
      -H "Authorization: Bearer $MATTERMOST_ADMIN_TOKEN" \
      -H 'Content-Type: application/json' \
      -d "$data"
  else
    curl -fsS -X "$method" "$url" \
      -H "Authorization: Bearer $MATTERMOST_ADMIN_TOKEN" \
      -H 'Content-Type: application/json'
  fi
}

require_rest_config() {
  [ -n "$MATTERMOST_URL" ] && [ -n "$MATTERMOST_ADMIN_TOKEN" ] && return
  log 'MATTERMOST_URL and MATTERMOST_ADMIN_TOKEN are required for REST object creation'
  exit 1
}

ensure_admin() {
  if ! mmctl user search "$MATTERMOST_ADMIN_USERNAME" >/dev/null 2>&1; then
    [ -n "$MATTERMOST_ADMIN_PASSWORD" ] || { log 'MATTERMOST_ADMIN_PASSWORD is required to create first admin'; exit 1; }
    mmctl user create --email "$MATTERMOST_ADMIN_EMAIL" --username "$MATTERMOST_ADMIN_USERNAME" --password "$MATTERMOST_ADMIN_PASSWORD" --system-admin --email-verified --disable-welcome-email
  elif [ "$MATTERMOST_SYNC_ADMIN_PASSWORD" = true ] && [ -n "$MATTERMOST_ADMIN_PASSWORD" ]; then
    mmctl user change-password "$MATTERMOST_ADMIN_USERNAME" --password "$MATTERMOST_ADMIN_PASSWORD" >/dev/null
  fi
  mmctl user email "$MATTERMOST_ADMIN_USERNAME" "$MATTERMOST_ADMIN_EMAIL" >/dev/null 2>&1 || true
  mmctl roles system-admin "$MATTERMOST_ADMIN_USERNAME" >/dev/null
}

ensure_integration_config() {
  mmctl config set ServiceSettings.EnableBotAccountCreation true >/dev/null
  mmctl config set ServiceSettings.EnableIncomingWebhooks true >/dev/null
  mmctl config set ServiceSettings.EnableCommands true >/dev/null
}

bridge_command_host() {
  python3 - "$1" <<'PY'
import sys
from urllib.parse import urlparse

parsed = urlparse(sys.argv[1])
print(parsed.hostname or "")
PY
}

host_needs_untrusted_allow() {
  python3 - "$1" <<'PY'
import ipaddress
import sys

host = sys.argv[1].strip("[]").lower()
if not host:
    raise SystemExit(1)
if host in {"localhost", "host.docker.internal"}:
    raise SystemExit(0)
try:
    ip = ipaddress.ip_address(host)
except ValueError:
    raise SystemExit(1)
if ip.is_loopback or ip.is_private or ip.is_link_local:
    raise SystemExit(0)
raise SystemExit(1)
PY
}

ensure_allowed_internal_connection() {
  local url="$1"
  local host
  host="$(bridge_command_host "$url")"
  [ -n "$host" ] || return
  if ! host_needs_untrusted_allow "$host"; then
    return
  fi
  local current
  current="$(mmctl config get ServiceSettings.AllowedUntrustedInternalConnections 2>/dev/null | tr -d '"' || true)"
  local updated
  updated="$(
    python3 - "$current" "$host" <<'PY'
import sys

items = [item.strip() for item in sys.argv[1].split(",") if item.strip()]
host = sys.argv[2]
if host not in items:
    items.append(host)
print(",".join(items))
PY
  )"
  if [ "$updated" != "$current" ]; then
    log "allowing Mattermost slash command access to internal bridge host: $host"
    mmctl config set ServiceSettings.AllowedUntrustedInternalConnections "$updated" >/dev/null
    MATTERMOST_RESTART_REQUIRED=true
  fi
}

wait_mattermost_ready() {
  for _ in $(seq 1 60); do
    if curl -fsS "$MATTERMOST_URL/api/v4/system/ping" >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  log 'Mattermost did not answer /api/v4/system/ping after restart'
  exit 1
}

restart_mattermost_if_needed() {
  [ "$MATTERMOST_RESTART_REQUIRED" = true ] || return
  [ "$MATTERMOST_RESTART_AFTER_INTERNAL_ALLOW" != false ] || return
  if command -v systemctl >/dev/null 2>&1 && sudo test -f /etc/systemd/system/ffc-ai-mattermost.service; then
    log 'restarting native Mattermost so AllowedUntrustedInternalConnections takes effect'
    sudo systemctl restart ffc-ai-mattermost.service
    wait_mattermost_ready
  elif [ -f "$INSTALL_DIR/docker-compose.yml" ] && grep -Eq '^[[:space:]]+mattermost:' "$INSTALL_DIR/docker-compose.yml"; then
    log 'restarting Mattermost container so AllowedUntrustedInternalConnections takes effect'
    (cd "$INSTALL_DIR" && compose restart mattermost)
    wait_mattermost_ready
  else
    log 'Mattermost restart skipped; restart Mattermost manually if /ai command execution is still blocked'
  fi
}

ensure_team_memberships() {
  local team="$1"
  local users=(
    "$MATTERMOST_ADMIN_USERNAME"
    ai-bridge
    master-writer-ai
    claude-code-ai
    codex-ai
    reviewer-ai-1
    reviewer-ai-2
    optional-specialist-ai
  )
  local channels=(town-square ai-ops ai-status ai-reviews ai-errors ai-archive)
  mmctl team users add "$team" "${users[@]}" >/dev/null 2>&1 || true
  for channel in "${channels[@]}"; do
    mmctl channel users add "$team:$channel" "${users[@]}" >/dev/null 2>&1 || true
  done
}

login_admin_token() {
  [ -n "$MATTERMOST_ADMIN_TOKEN" ] && return
  [ -n "$MATTERMOST_URL" ] && [ -n "$MATTERMOST_ADMIN_PASSWORD" ] || return
  MATTERMOST_ADMIN_TOKEN="$(
    curl -fsS -i -X POST "$MATTERMOST_URL/api/v4/users/login" \
      -H 'Content-Type: application/json' \
      -d "{\"login_id\":\"$MATTERMOST_ADMIN_USERNAME\",\"password\":\"$MATTERMOST_ADMIN_PASSWORD\"}" |
      python3 -c 'import sys
token = ""
for line in sys.stdin:
    if line.lower().startswith("token:"):
        token = line.split(":", 1)[1].strip()
        break
print(token)'
  )"
  [ -n "$MATTERMOST_ADMIN_TOKEN" ] || { log 'admin REST login did not return a token'; exit 1; }
}

log 'creating Mattermost team/channels/bot identities with mmctl --local'
if ! mmctl version >/dev/null 2>&1; then
  log 'mmctl not available; bootstrap requires a healthy Mattermost service'
  exit 1
fi
ensure_admin
ensure_integration_config
ensure_team ai-lab "AI Lab"
for channel in ai-ops ai-status ai-reviews ai-errors ai-archive; do
  ensure_channel ai-lab "$channel" "$channel"
done
login_admin_token
require_rest_config
for bot in ai-bridge master-writer-ai claude-code-ai codex-ai reviewer-ai-1 reviewer-ai-2 optional-specialist-ai; do
  ensure_bot "$bot" "$bot"
done
ensure_team_memberships ai-lab

rest_json GET "$MATTERMOST_URL/api/v4/users/me" >/dev/null || {
  log 'MATTERMOST_ADMIN_TOKEN validation failed'
  exit 1
}
TEAM_ID="$(rest_json GET "$MATTERMOST_URL/api/v4/teams/name/ai-lab" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')"
SLASH_STATUS="pending_bridge_command_url"
SLASH_TOKEN_CONFIGURED=false
SLASH_COMMAND_URL=""
if [ -n "$BRIDGE_COMMAND_URL" ]; then
  log 'creating /ai slash command through Mattermost REST API'
  ensure_allowed_internal_connection "$BRIDGE_COMMAND_URL"
  EXISTING_COMMAND="$(
    rest_json GET "$MATTERMOST_URL/api/v4/commands?team_id=$TEAM_ID" |
      python3 -c 'import json,sys
commands = json.load(sys.stdin)
for command in commands:
    if command.get("trigger") == "ai":
        print(json.dumps(command))
        break'
  )"
  if [ -z "$EXISTING_COMMAND" ]; then
    rest_json POST "$MATTERMOST_URL/api/v4/commands" "{\"team_id\":\"$TEAM_ID\",\"trigger\":\"ai\",\"url\":\"$BRIDGE_COMMAND_URL\",\"method\":\"P\",\"display_name\":\"AI Bridge\",\"description\":\"Route /ai commands to AI remote runner\"}" >/dev/null
  else
    COMMAND_ID="$(printf '%s' "$EXISTING_COMMAND" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("id", ""))')"
    EXISTING_URL="$(printf '%s' "$EXISTING_COMMAND" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("url", ""))')"
    if [ "$EXISTING_URL" != "$BRIDGE_COMMAND_URL" ]; then
      rest_json DELETE "$MATTERMOST_URL/api/v4/commands/$COMMAND_ID" >/dev/null
      rest_json POST "$MATTERMOST_URL/api/v4/commands" "{\"team_id\":\"$TEAM_ID\",\"trigger\":\"ai\",\"url\":\"$BRIDGE_COMMAND_URL\",\"method\":\"P\",\"display_name\":\"AI Bridge\",\"description\":\"Route /ai commands to AI remote runner\"}" >/dev/null
    fi
  fi
  COMMAND_JSON="$(
    rest_json GET "$MATTERMOST_URL/api/v4/commands?team_id=$TEAM_ID" |
      python3 -c 'import json,sys
commands = json.load(sys.stdin)
for command in commands:
    if command.get("trigger") == "ai":
        print(json.dumps(command))
        break'
  )"
  [ -n "$COMMAND_JSON" ] || {
    log '/ai slash command validation failed'
    exit 1
  }
  COMMAND_ID="$(printf '%s' "$COMMAND_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("id", ""))')"
  [ -n "$COMMAND_ID" ] || { log '/ai slash command id was not returned by Mattermost REST API'; exit 1; }
  COMMAND_JSON="$(rest_json GET "$MATTERMOST_URL/api/v4/commands/$COMMAND_ID")"
  SLASH_TOKEN="$(printf '%s' "$COMMAND_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("token", ""))')"
  [ -n "$SLASH_TOKEN" ] || { log '/ai slash command token was not returned by Mattermost REST API'; exit 1; }
  TMP_ENV="$(mktemp)"
  if [ -f "$INSTALL_DIR/.env" ]; then
    sudo awk -F= '$1 != "MATTERMOST_SLASH_TOKEN"' "$INSTALL_DIR/.env" > "$TMP_ENV"
  fi
  printf 'MATTERMOST_SLASH_TOKEN=%s\n' "$SLASH_TOKEN" >> "$TMP_ENV"
  sudo cp "$TMP_ENV" "$INSTALL_DIR/.env"
  rm -f "$TMP_ENV"
  sudo chmod 0600 "$INSTALL_DIR/.env"
  SLASH_STATUS="ready"
  SLASH_TOKEN_CONFIGURED=true
  SLASH_COMMAND_URL="$BRIDGE_COMMAND_URL"
else
  log 'BRIDGE_COMMAND_URL not set; deferring /ai slash command creation until runner pairing'
fi

log 'creating incoming webhook through Mattermost REST API'
HOOK_ID=""
if [ -f "$MANIFEST" ]; then
  HOOK_ID="$(python3 - "$MANIFEST" <<'PY'
import json
import sys
from pathlib import Path
path = Path(sys.argv[1])
try:
    print(json.loads(path.read_text(encoding="utf-8")).get("incoming_webhook_id", ""))
except json.JSONDecodeError:
    print("")
PY
)"
fi
if [ -z "$HOOK_ID" ]; then
  if [ -z "$WEBHOOK_CHANNEL_ID" ]; then
    WEBHOOK_CHANNEL_ID="$(rest_json GET "$MATTERMOST_URL/api/v4/teams/$TEAM_ID/channels/name/ai-status" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')"
  fi
  HOOK_ID="$(rest_json POST "$MATTERMOST_URL/api/v4/hooks/incoming" "{\"channel_id\":\"$WEBHOOK_CHANNEL_ID\",\"display_name\":\"AI Status\",\"description\":\"AI runner status events\"}" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("id", ""))')"
fi
[ -n "$HOOK_ID" ] || { log 'incoming webhook creation did not return an id'; exit 1; }
sudo tee "$MANIFEST" >/dev/null <<EOF
{
  "team": "ai-lab",
  "channels": ["ai-ops", "ai-status", "ai-reviews", "ai-errors", "ai-archive"],
  "bots": ["ai-bridge", "master-writer-ai", "claude-code-ai", "codex-ai", "reviewer-ai-1", "reviewer-ai-2", "optional-specialist-ai"],
  "slash_command": "/ai",
  "slash_command_status": "$SLASH_STATUS",
  "slash_command_url": "$SLASH_COMMAND_URL",
  "slash_command_token_configured": $SLASH_TOKEN_CONFIGURED,
  "incoming_webhook_id": "$HOOK_ID",
  "status": "ready"
}
EOF
sudo chmod 0600 "$MANIFEST"
restart_mattermost_if_needed
log "wrote $MANIFEST"
