#!/usr/bin/env bash
set -euo pipefail

STATE_ROOT="${AI_REMOTE_STATE:-/var/lib/ai-remote-runner}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLATFORM_URL=""
WEBHOOK_URL=""
BOT_TOKEN=""
BRIDGE_SECRET=""
BRIDGE_SECRET_FILE=""
BRIDGE_SECRET_STDIN=false
SLASH_TOKEN=""
SLASH_TOKEN_FILE=""
SLASH_TOKEN_STDIN=false
TRANSFER_METHOD=""

usage() {
  printf 'usage: %s --platform-url URL --webhook-url URL --transfer-method ssh|broker|manual-secure [--bot-token TOKEN] [--bridge-secret-file PATH|--bridge-secret-stdin] [--slash-token-file PATH|--slash-token-stdin]\n' "$0"
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --platform-url) PLATFORM_URL="$2"; shift ;;
    --webhook-url) WEBHOOK_URL="$2"; shift ;;
    --bot-token) BOT_TOKEN="$2"; shift ;;
    --bridge-secret-file) BRIDGE_SECRET_FILE="$2"; shift ;;
    --bridge-secret-stdin) BRIDGE_SECRET_STDIN=true ;;
    --slash-token-file) SLASH_TOKEN_FILE="$2"; shift ;;
    --slash-token-stdin) SLASH_TOKEN_STDIN=true ;;
    --transfer-method) TRANSFER_METHOD="$2"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage; exit 2 ;;
  esac
  shift
done

[ -n "$PLATFORM_URL" ] && [ -n "$WEBHOOK_URL" ] && [ -n "$TRANSFER_METHOD" ] || { usage; exit 2; }
case "$TRANSFER_METHOD" in
  ssh|broker|manual-secure) ;;
  *) printf '[pair-runner] --transfer-method must be ssh, broker, or manual-secure\n' >&2; exit 2 ;;
esac
if [ -n "$BRIDGE_SECRET_FILE" ]; then
  BRIDGE_SECRET="$(tr -d '\r\n' < "$BRIDGE_SECRET_FILE")"
elif [ "$BRIDGE_SECRET_STDIN" = true ]; then
  BRIDGE_SECRET="$(tr -d '\r\n')"
fi
if [ -n "$SLASH_TOKEN_FILE" ]; then
  SLASH_TOKEN="$(tr -d '\r\n' < "$SLASH_TOKEN_FILE")"
elif [ "$SLASH_TOKEN_STDIN" = true ]; then
  SLASH_TOKEN="$(tr -d '\r\n')"
fi
[ -n "$BRIDGE_SECRET" ] || { usage; exit 2; }
[ -n "$SLASH_TOKEN" ] || { printf '[pair-runner] Mattermost slash token is required; use --slash-token-file or --slash-token-stdin\n' >&2; exit 2; }
case "$BRIDGE_SECRET" in
  *[!A-Za-z0-9_-]*)
    printf '[pair-runner] bridge secret must be base64url characters only\n' >&2
    exit 2
    ;;
esac
printf '%s' "$BRIDGE_SECRET" | python3 -c '
import base64
import sys

secret = sys.stdin.read()
try:
    raw = base64.urlsafe_b64decode((secret + "=" * (-len(secret) % 4)).encode("ascii"))
except Exception:
    raise SystemExit("invalid bridge secret encoding")
if len(raw) < 32:
    raise SystemExit("bridge secret must decode to at least 256 bits")
'

sudo mkdir -p "$STATE_ROOT"
if [ -f "$STATE_ROOT/config.env" ]; then
  TMP_CONFIG="$(mktemp)"
  sudo awk -F= '
    $1 != "MATTERMOST_PLATFORM_URL" &&
    $1 != "MATTERMOST_WEBHOOK_URL" &&
    $1 != "MATTERMOST_BOT_TOKEN" &&
    $1 != "MATTERMOST_SLASH_TOKEN" &&
    $1 != "AI_BRIDGE_SHARED_SECRET" &&
    $1 != "AI_BRIDGE_SECRET_TRANSFER_METHOD"
  ' "$STATE_ROOT/config.env" > "$TMP_CONFIG"
  sudo cp "$TMP_CONFIG" "$STATE_ROOT/config.env"
  rm -f "$TMP_CONFIG"
fi
sudo tee -a "$STATE_ROOT/config.env" >/dev/null <<EOF
MATTERMOST_PLATFORM_URL=$PLATFORM_URL
MATTERMOST_WEBHOOK_URL=$WEBHOOK_URL
MATTERMOST_SLASH_TOKEN=$SLASH_TOKEN
EOF
if [ -n "$BOT_TOKEN" ]; then
  printf 'MATTERMOST_BOT_TOKEN=%s\n' "$BOT_TOKEN" | sudo tee -a "$STATE_ROOT/config.env" >/dev/null
fi
sudo tee -a "$STATE_ROOT/config.env" >/dev/null <<EOF
AI_BRIDGE_SHARED_SECRET=$BRIDGE_SECRET
AI_BRIDGE_SECRET_TRANSFER_METHOD=$TRANSFER_METHOD
EOF
sudo chmod 0600 "$STATE_ROOT/config.env"
printf '[pair-runner] pairing config written; running bridge loopback validation\n'
if [ "${PAIR_RUNNER_SKIP_VALIDATE:-false}" != true ]; then
  AI_REMOTE_STATE="$STATE_ROOT" "$SCRIPT_DIR/validate-core-ready.sh"
fi
