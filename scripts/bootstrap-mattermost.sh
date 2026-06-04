#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="${MATTERMOST_INSTALL_DIR:-/opt/ffc-ai-mattermost}"
MANIFEST="$INSTALL_DIR/mattermost-objects.json"
MATTERMOST_URL="${MATTERMOST_URL:-}"
MATTERMOST_ADMIN_TOKEN="${MATTERMOST_ADMIN_TOKEN:-}"
BRIDGE_COMMAND_URL="${BRIDGE_COMMAND_URL:-}"
WEBHOOK_CHANNEL_ID="${WEBHOOK_CHANNEL_ID:-}"

log() {
  printf '[bootstrap-mattermost] %s\n' "$*"
}

mmctl() {
  (cd "$INSTALL_DIR" && compose exec -T mattermost mmctl --local "$@")
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
  mmctl team list | grep -q " $name " || mmctl team create --name "$name" --display-name "$display"
  require_mmctl_match "team:$name" " $name " team list
}

ensure_channel() {
  local team="$1"
  local name="$2"
  local display="$3"
  mmctl channel list "$team" | grep -q " $name " || mmctl channel create --team "$team" --name "$name" --display-name "$display"
  require_mmctl_match "channel:$team/$name" " $name " channel list "$team"
}

ensure_bot() {
  local username="$1"
  local display="$2"
  if ! mmctl bot list | grep -q " $username "; then
    mmctl bot create "$username" --display-name "$display" --description "FFC-AI bot identity"
  fi
  require_mmctl_match "bot:$username" " $username " bot list
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
  [ -n "$MATTERMOST_URL" ] && [ -n "$MATTERMOST_ADMIN_TOKEN" ] && [ -n "$BRIDGE_COMMAND_URL" ] && return
  log 'MATTERMOST_URL, MATTERMOST_ADMIN_TOKEN, and BRIDGE_COMMAND_URL are required to create /ai and the incoming webhook'
  exit 1
}

log 'creating Mattermost team/channels/bot identities with mmctl --local'
if ! (cd "$INSTALL_DIR" && compose exec -T mattermost mmctl version >/dev/null 2>&1); then
  log 'mmctl not available in Mattermost container; bootstrap requires a healthy Mattermost container'
  exit 1
fi
ensure_team ai-lab "AI Lab"
for channel in ai-ops ai-status ai-reviews ai-errors ai-archive; do
  ensure_channel ai-lab "$channel" "$channel"
done
for bot in ai-bridge master-writer-ai claude-code-ai codex-ai reviewer-ai-1 reviewer-ai-2 optional-specialist-ai; do
  ensure_bot "$bot" "$bot"
done

log 'slash command and incoming webhook require admin token on Mattermost editions without mmctl local integration support'
log 'if REST fallback is used, create or log in as a Mattermost admin first and export MATTERMOST_ADMIN_TOKEN'
require_rest_config
rest_json GET "$MATTERMOST_URL/api/v4/users/me" >/dev/null || {
  log 'MATTERMOST_ADMIN_TOKEN validation failed'
  exit 1
}
log 'creating /ai slash command through Mattermost REST API'
TEAM_ID="$(rest_json GET "$MATTERMOST_URL/api/v4/teams/name/ai-lab" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')"
if ! rest_json GET "$MATTERMOST_URL/api/v4/commands/team/$TEAM_ID/custom" | python3 -c 'import json,sys; sys.exit(0 if any(c.get("trigger") == "ai" for c in json.load(sys.stdin)) else 1)'; then
  rest_json POST "$MATTERMOST_URL/api/v4/commands" "{\"team_id\":\"$TEAM_ID\",\"trigger\":\"ai\",\"url\":\"$BRIDGE_COMMAND_URL\",\"method\":\"P\",\"display_name\":\"AI Bridge\",\"description\":\"Route /ai commands to AI remote runner\"}" >/dev/null
fi
rest_json GET "$MATTERMOST_URL/api/v4/commands/team/$TEAM_ID/custom" | python3 -c 'import json,sys; sys.exit(0 if any(c.get("trigger") == "ai" for c in json.load(sys.stdin)) else 1)' || {
  log '/ai slash command validation failed'
  exit 1
}

log 'creating incoming webhook through Mattermost REST API'
if [ -z "$WEBHOOK_CHANNEL_ID" ]; then
  WEBHOOK_CHANNEL_ID="$(rest_json GET "$MATTERMOST_URL/api/v4/teams/$TEAM_ID/channels/name/ai-status" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')"
fi
HOOK_ID="$(rest_json POST "$MATTERMOST_URL/api/v4/hooks/incoming" "{\"channel_id\":\"$WEBHOOK_CHANNEL_ID\",\"display_name\":\"AI Status\",\"description\":\"AI runner status events\"}" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("id", ""))')"
[ -n "$HOOK_ID" ] || { log 'incoming webhook creation did not return an id'; exit 1; }
sudo tee "$MANIFEST" >/dev/null <<EOF
{
  "team": "ai-lab",
  "channels": ["ai-ops", "ai-status", "ai-reviews", "ai-errors", "ai-archive"],
  "bots": ["ai-bridge", "master-writer-ai", "claude-code-ai", "codex-ai", "reviewer-ai-1", "reviewer-ai-2", "optional-specialist-ai"],
  "slash_command": "/ai",
  "incoming_webhook_id": "$HOOK_ID",
  "status": "ready"
}
EOF
sudo chmod 0600 "$MANIFEST"
log "wrote $MANIFEST"
