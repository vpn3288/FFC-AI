#!/usr/bin/env bash
set -euo pipefail

STATE_ROOT="${AI_REMOTE_STATE:-/var/lib/ai-remote-runner}"
TELEGRAM_SYSTEMD_UNIT="${TELEGRAM_SYSTEMD_UNIT:-/etc/systemd/system/ai-telegram-bot.service}"
BOT_TOKEN=""
BOT_TOKEN_FILE=""
BOT_TOKEN_STDIN=false
CHAT_IDS=""
ALLOW_ALL_CHATS=false
DISCOVER_CHAT_ID=false
API_BASE=""
RESERVED_USD=""

usage() {
  printf 'usage: %s [--bot-token TOKEN|--bot-token-file PATH|--bot-token-stdin] [--telegram-id ID|--chat-id ID[,ID...]|--allow-all-chats|--discover-chat-id] [--api-base URL] [--reserved-usd USD]\n' "$0"
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --bot-token) BOT_TOKEN="$2"; shift ;;
    --bot-token-file) BOT_TOKEN_FILE="$2"; shift ;;
    --bot-token-stdin) BOT_TOKEN_STDIN=true ;;
    --telegram-id) CHAT_IDS="${CHAT_IDS:+$CHAT_IDS,}$2"; shift ;;
    --chat-id) CHAT_IDS="${CHAT_IDS:+$CHAT_IDS,}$2"; shift ;;
    --allow-all-chats) ALLOW_ALL_CHATS=true ;;
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

[ -n "$BOT_TOKEN" ] || { printf '[pair-telegram] Telegram bot token is required; use --bot-token-file or --bot-token-stdin\n' >&2; exit 2; }
if [ "$ALLOW_ALL_CHATS" != true ] && [ "$DISCOVER_CHAT_ID" != true ] && [ -z "$CHAT_IDS" ]; then
  printf '[pair-telegram] --chat-id is required unless --allow-all-chats or --discover-chat-id is set\n' >&2
  exit 2
fi
case "$BOT_TOKEN" in
  *[!A-Za-z0-9_:-]*)
    printf '[pair-telegram] Telegram bot token contains unsupported characters\n' >&2
    exit 2
    ;;
esac

sudo mkdir -p "$STATE_ROOT"
if [ -f "$STATE_ROOT/config.env" ]; then
  TMP_CONFIG="$(mktemp)"
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
TELEGRAM_ALLOW_ALL_CHATS=$ALLOW_ALL_CHATS
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
