#!/usr/bin/env bash
set -euo pipefail

STATE_ROOT="${AI_REMOTE_STATE:-/var/lib/ai-remote-runner}"
TELEGRAM_SYSTEMD_UNIT="${TELEGRAM_SYSTEMD_UNIT:-/etc/systemd/system/ai-telegram-bot.service}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VALIDATE_CORE_READY_SCRIPT="${VALIDATE_CORE_READY_SCRIPT:-$SCRIPT_DIR/validate-core-ready.sh}"
BOT_TOKEN=""
BOT_TOKEN_FILE=""
BOT_TOKEN_STDIN=false
CHAT_IDS=""
DISCOVER_CHAT_ID=false
API_BASE=""
RESERVED_USD=""

usage() {
  printf 'usage: %s [--bot-token TOKEN|--bot-token-file PATH|--bot-token-stdin] [--telegram-id ID|--chat-id ID[,ID...]|--discover-chat-id] [--api-base URL] [--reserved-usd USD]\n' "$0"
}

validate_chat_ids() {
  python3 - "$CHAT_IDS" <<'PY'
import re
import sys

raw = sys.argv[1]
items = raw.split(",") if raw else []
if not items or any(not item for item in items):
    raise SystemExit(1)
pattern = re.compile(r"^-?[0-9]{1,32}$")
if any(not pattern.fullmatch(item) for item in items):
    raise SystemExit(1)
PY
}

validate_optional_value() {
  local label="$1"
  local value="$2"
  case "$value" in
    *$'\n'*|*$'\r'*)
      printf '[pair-telegram] %s must be a single-line value\n' "$label" >&2
      exit 2
      ;;
  esac
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --bot-token) BOT_TOKEN="$2"; shift ;;
    --bot-token-file) BOT_TOKEN_FILE="$2"; shift ;;
    --bot-token-stdin) BOT_TOKEN_STDIN=true ;;
    --telegram-id) CHAT_IDS="${CHAT_IDS:+$CHAT_IDS,}$2"; shift ;;
    --chat-id) CHAT_IDS="${CHAT_IDS:+$CHAT_IDS,}$2"; shift ;;
    --discover-chat-id) DISCOVER_CHAT_ID=true ;;
    --api-base) API_BASE="$2"; shift ;;
    --reserved-usd) RESERVED_USD="$2"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage; exit 2 ;;
  esac
  shift
done

if [ -n "$BOT_TOKEN_FILE" ]; then
  BOT_TOKEN="$(tr -d '\r\n' < "$BOT_TOKEN_FILE")"
elif [ "$BOT_TOKEN_STDIN" = true ]; then
  BOT_TOKEN="$(tr -d '\r\n')"
fi

if [ -z "$BOT_TOKEN" ]; then
  if [ -t 0 ] || [ -n "$CHAT_IDS" ] || [ "$DISCOVER_CHAT_ID" = true ]; then
    printf '[pair-telegram] Telegram bot token: ' >&2
    stty_state="$(stty -g 2>/dev/null || true)"
    stty -echo 2>/dev/null || true
    if ! IFS= read -r BOT_TOKEN; then
      BOT_TOKEN=""
    fi
    if [ -n "$stty_state" ]; then
      stty "$stty_state" 2>/dev/null || true
    else
      stty echo 2>/dev/null || true
    fi
    printf '\n' >&2
    BOT_TOKEN="$(printf '%s' "$BOT_TOKEN" | tr -d '\r\n')"
  fi
fi
[ -n "$BOT_TOKEN" ] || { printf '[pair-telegram] Telegram bot token is required; enter it when prompted or use --bot-token-file/--bot-token-stdin\n' >&2; exit 2; }
if [ "$DISCOVER_CHAT_ID" != true ] && [ -z "$CHAT_IDS" ]; then
  printf '[pair-telegram] --telegram-id or --chat-id is required unless --discover-chat-id is set\n' >&2
  exit 2
fi
case "$BOT_TOKEN" in
  *[!A-Za-z0-9_:-]*)
    printf '[pair-telegram] Telegram bot token contains unsupported characters\n' >&2
    exit 2
    ;;
esac
if [ -n "$CHAT_IDS" ] && ! validate_chat_ids; then
  printf '[pair-telegram] Telegram ID/chat_id must be a comma-separated list of numeric chat IDs\n' >&2
  exit 2
fi
if [ -n "$API_BASE" ]; then
  validate_optional_value "--api-base" "$API_BASE"
  case "$API_BASE" in
    http://*|https://*) ;;
    *) printf '[pair-telegram] --api-base must start with http:// or https://\n' >&2; exit 2 ;;
  esac
fi
if [ -n "$RESERVED_USD" ]; then
  validate_optional_value "--reserved-usd" "$RESERVED_USD"
  if ! python3 - "$RESERVED_USD" <<'PY'
import sys

try:
    value = float(sys.argv[1])
except ValueError:
    raise SystemExit(1)
if value < 0:
    raise SystemExit(1)
PY
  then
    printf '[pair-telegram] --reserved-usd must be a non-negative number\n' >&2
    exit 2
  fi
fi

sudo mkdir -p "$STATE_ROOT"
if [ -f "$STATE_ROOT/config.env" ]; then
  TMP_CONFIG="$(mktemp)"
  # Remove stale all-chat config from older installs; execution always requires explicit chat IDs.
  sudo awk -F= '
    $1 != "TELEGRAM_BOT_TOKEN" &&
    $1 != "TELEGRAM_ALLOWED_CHAT_IDS" &&
    $1 != "TELEGRAM_ALLOW_ALL_CHATS" &&
    $1 != "TELEGRAM_API_BASE" &&
    $1 != "TELEGRAM_RESERVED_USD"
  ' "$STATE_ROOT/config.env" > "$TMP_CONFIG"
  sudo cp "$TMP_CONFIG" "$STATE_ROOT/config.env"
  rm -f "$TMP_CONFIG"
fi
sudo tee -a "$STATE_ROOT/config.env" >/dev/null <<EOF
TELEGRAM_BOT_TOKEN=$BOT_TOKEN
TELEGRAM_ALLOWED_CHAT_IDS=$CHAT_IDS
EOF
if [ -n "$API_BASE" ]; then
  printf 'TELEGRAM_API_BASE=%s\n' "$API_BASE" | sudo tee -a "$STATE_ROOT/config.env" >/dev/null
fi
if [ -n "$RESERVED_USD" ]; then
  printf 'TELEGRAM_RESERVED_USD=%s\n' "$RESERVED_USD" | sudo tee -a "$STATE_ROOT/config.env" >/dev/null
fi
sudo chmod 0600 "$STATE_ROOT/config.env"

printf '[pair-telegram] Telegram pairing config written\n'
if [ "$DISCOVER_CHAT_ID" = true ]; then
  printf '[pair-telegram] discovery mode enabled; send any message to the bot and it will reply with chat_id without running AI commands\n'
fi
if command -v systemctl >/dev/null 2>&1 && [ -f "$TELEGRAM_SYSTEMD_UNIT" ]; then
  sudo systemctl daemon-reload
  sudo systemctl enable --now ai-telegram-bot.service
  sudo systemctl restart ai-telegram-bot.service
  printf '[pair-telegram] ai-telegram-bot.service started\n'
else
  printf '[pair-telegram] systemd service not found; run /opt/ai-remote-runner/run-telegram-local.sh manually on non-systemd installs\n'
fi

if [ "${PAIR_TELEGRAM_VALIDATE_CORE_READY:-true}" = true ] && { [ -x "$VALIDATE_CORE_READY_SCRIPT" ] || [ -f "$VALIDATE_CORE_READY_SCRIPT" ]; }; then
  printf '[pair-telegram] running core readiness validation for Telegram channel\n'
  sudo env AI_REMOTE_STATE="$STATE_ROOT" bash "$VALIDATE_CORE_READY_SCRIPT"
else
  printf '[pair-telegram] core readiness validation skipped; run validate-core-ready.sh before treating Telegram as ready\n'
fi
