#!/usr/bin/env bash
set -euo pipefail

STATE_ROOT="${AI_REMOTE_STATE:-/var/lib/ai-remote-runner}"
PLATFORM_URL=""
WEBHOOK_URL=""
BOT_TOKEN=""
BRIDGE_SECRET=""

usage() {
  printf 'usage: %s --platform-url URL --webhook-url URL --bot-token TOKEN --bridge-secret SECRET\n' "$0"
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --platform-url) PLATFORM_URL="$2"; shift ;;
    --webhook-url) WEBHOOK_URL="$2"; shift ;;
    --bot-token) BOT_TOKEN="$2"; shift ;;
    --bridge-secret) BRIDGE_SECRET="$2"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage; exit 2 ;;
  esac
  shift
done

[ -n "$PLATFORM_URL" ] && [ -n "$WEBHOOK_URL" ] && [ -n "$BOT_TOKEN" ] && [ -n "$BRIDGE_SECRET" ] || { usage; exit 2; }

sudo mkdir -p "$STATE_ROOT"
sudo tee -a "$STATE_ROOT/config.env" >/dev/null <<EOF
MATTERMOST_PLATFORM_URL=$PLATFORM_URL
MATTERMOST_WEBHOOK_URL=$WEBHOOK_URL
MATTERMOST_BOT_TOKEN=$BOT_TOKEN
AI_BRIDGE_SHARED_SECRET=$BRIDGE_SECRET
EOF
sudo chmod 0600 "$STATE_ROOT/config.env"
printf '[pair-runner] pairing config written; run bridge loopback smoke test next\n'
