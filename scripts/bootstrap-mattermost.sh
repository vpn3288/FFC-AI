#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="${MATTERMOST_INSTALL_DIR:-/opt/ffc-ai-mattermost}"
MANIFEST="$INSTALL_DIR/mattermost-objects.json"

log() {
  printf '[bootstrap-mattermost] %s\n' "$*"
}

mmctl() {
  (cd "$INSTALL_DIR" && sudo docker compose exec -T mattermost mmctl --local "$@")
}

ensure_team() {
  local name="$1"
  local display="$2"
  mmctl team list | grep -q " $name " || mmctl team create --name "$name" --display-name "$display"
}

ensure_channel() {
  local team="$1"
  local name="$2"
  local display="$3"
  mmctl channel list "$team" | grep -q " $name " || mmctl channel create --team "$team" --name "$name" --display-name "$display"
}

ensure_bot() {
  local username="$1"
  local display="$2"
  if ! mmctl bot list | grep -q " $username "; then
    mmctl bot create "$username" --display-name "$display" --description "FFC-AI bot identity"
  fi
}

log 'creating Mattermost team/channels/bot identities with mmctl --local'
ensure_team ai-lab "AI Lab"
for channel in ai-ops ai-status ai-reviews ai-errors ai-archive; do
  ensure_channel ai-lab "$channel" "$channel"
done
for bot in ai-bridge master-writer-ai claude-code-ai codex-ai reviewer-ai-1 reviewer-ai-2 optional-specialist-ai; do
  ensure_bot "$bot" "$bot"
done

log 'slash command and incoming webhook require admin token on Mattermost editions without mmctl local integration support'
sudo tee "$MANIFEST" >/dev/null <<EOF
{
  "team": "ai-lab",
  "channels": ["ai-ops", "ai-status", "ai-reviews", "ai-errors", "ai-archive"],
  "bots": ["ai-bridge", "master-writer-ai", "claude-code-ai", "codex-ai", "reviewer-ai-1", "reviewer-ai-2", "optional-specialist-ai"],
  "slash_command": "/ai",
  "incoming_webhook": "required",
  "status": "partial_until_slash_command_and_webhook_created"
}
EOF
sudo chmod 0600 "$MANIFEST"
log "wrote $MANIFEST"
