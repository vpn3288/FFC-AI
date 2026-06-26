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
NORMALIZED_RESERVED_USD=""
STATUS_INTERVAL_SECONDS="${TELEGRAM_STATUS_INTERVAL_SECONDS:-5}"
STATUS_MIN_UPDATE_SECONDS="${TELEGRAM_STATUS_MIN_UPDATE_SECONDS:-0.8}"
SYNC_COMMANDS_ON_STARTUP="${TELEGRAM_SYNC_COMMANDS_ON_STARTUP:-1}"
ALLOWED_UPDATES="${TELEGRAM_ALLOWED_UPDATES:-message,edited_message,callback_query}"
NATIVE_DRAFT_PROGRESS="${TELEGRAM_NATIVE_DRAFT_PROGRESS:-0}"
NATIVE_DRAFT_ALLOW_CHAT_IDS="${TELEGRAM_NATIVE_DRAFT_ALLOW_CHAT_IDS:-}"
GROUP_MODE="${TELEGRAM_GROUP_MODE:-mention}"
VERIFY_TELEGRAM="${PAIR_TELEGRAM_VERIFY_API:-true}"
DELETE_WEBHOOK="${PAIR_TELEGRAM_DELETE_WEBHOOK:-true}"
DISCOVER_TIMEOUT_SECONDS="${PAIR_TELEGRAM_DISCOVER_TIMEOUT_SECONDS:-45}"
BOT_INFO_JSON=""

inside_systemd_unit() {
  local unit="$1"
  grep -q -- "$unit" "/proc/$$/cgroup" 2>/dev/null
}

defer_service_restart() {
  local unit="$1"
  local pending_file="$STATE_ROOT/pending-service-restart.txt"
  local timestamp
  timestamp="$(date -Is)"
  sudo mkdir -p "$STATE_ROOT"
  if [ -s "$pending_file" ] && sudo grep -q "^unit=$unit$" "$pending_file"; then
    printf '[pair-telegram] restart already deferred; run after this task finishes: sudo systemctl restart %s\n' "$unit"
    return 0
  fi
  {
    printf '[restart:%s]\n' "$timestamp"
    printf 'unit=%s\n' "$unit"
    printf 'reason=%s\n' "avoid terminating the active Telegram/Claude task with SIGTERM/returncode=143"
    printf 'created_at=%s\n' "$timestamp"
    printf 'command=sudo systemctl restart %s\n' "$unit"
    printf '\n'
  } | sudo tee -a "$pending_file" >/dev/null
  sudo chmod 0600 "$pending_file"
  printf '[pair-telegram] restart deferred because this script is running inside ai-telegram-bot.service; run after this task finishes: sudo systemctl restart %s\n' "$unit"
}

restart_or_defer_telegram_service() {
  sudo systemctl enable --now ai-telegram-bot.service
  if inside_systemd_unit "ai-telegram-bot.service"; then
    defer_service_restart ai-telegram-bot.service
  else
    sudo systemctl restart ai-telegram-bot.service
  fi
}

usage() {
  printf 'usage: %s [--bot-token TOKEN|--bot-token-file PATH|--bot-token-stdin] [--telegram-id ID|--chat-id ID[,ID...]|--discover-chat-id] [--api-base URL] [--reserved-usd USD] [--status-interval SECONDS] [--status-min-update SECONDS] [--group-mode mention|reply|all] [--native-draft-progress] [--keep-webhook]\n' "$0"
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

normalize_reserved_usd() {
  python3 - "$1" <<'PY'
import sys

raw = sys.argv[1].strip()
if raw.lower() in {"", "0", "off", "none", "no", "false", "unlimited", "infinite", "inf", "无限", "不限", "关闭"}:
    print("0")
    raise SystemExit(0)
try:
    value = float(raw)
except ValueError:
    raise SystemExit(1)
if value < 0:
    raise SystemExit(1)
print((f"{value:.6f}".rstrip("0").rstrip(".")) or "0")
PY
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
    --status-interval) STATUS_INTERVAL_SECONDS="$2"; shift ;;
    --status-min-update) STATUS_MIN_UPDATE_SECONDS="$2"; shift ;;
    --native-draft-progress) NATIVE_DRAFT_PROGRESS=1 ;;
    --no-native-draft-progress) NATIVE_DRAFT_PROGRESS=0 ;;
    --native-draft-allow-chat-id) NATIVE_DRAFT_ALLOW_CHAT_IDS="${NATIVE_DRAFT_ALLOW_CHAT_IDS:+$NATIVE_DRAFT_ALLOW_CHAT_IDS,}$2"; shift ;;
    --group-mode) GROUP_MODE="$2"; shift ;;
    --no-command-menu) SYNC_COMMANDS_ON_STARTUP=0 ;;
    --command-menu) SYNC_COMMANDS_ON_STARTUP=1 ;;
    --keep-webhook) DELETE_WEBHOOK=false ;;
    --delete-webhook) DELETE_WEBHOOK=true ;;
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
  if ! NORMALIZED_RESERVED_USD="$(normalize_reserved_usd "$RESERVED_USD")"; then
    printf '[pair-telegram] --reserved-usd must be a non-negative number, 0, unlimited, or 无限\n' >&2
    exit 2
  fi
fi
for numeric_pair in "status-interval:$STATUS_INTERVAL_SECONDS" "status-min-update:$STATUS_MIN_UPDATE_SECONDS"; do
  label="${numeric_pair%%:*}"
  value="${numeric_pair#*:}"
  validate_optional_value "--$label" "$value"
  if ! python3 - "$value" <<'PY'
import sys

try:
    value = float(sys.argv[1])
except ValueError:
    raise SystemExit(1)
if value < 0:
    raise SystemExit(1)
PY
  then
    printf '[pair-telegram] --%s must be a non-negative number\n' "$label" >&2
    exit 2
  fi
done
validate_optional_value "TELEGRAM_ALLOWED_UPDATES" "$ALLOWED_UPDATES"
validate_optional_value "TELEGRAM_NATIVE_DRAFT_ALLOW_CHAT_IDS" "$NATIVE_DRAFT_ALLOW_CHAT_IDS"
validate_optional_value "TELEGRAM_GROUP_MODE" "$GROUP_MODE"
case "$GROUP_MODE" in
  mention|mentions|reply|replies|all|any) ;;
  *) printf '[pair-telegram] --group-mode must be mention, reply, or all\n' >&2; exit 2 ;;
esac

telegram_api_base="${API_BASE:-https://api.telegram.org}"
telegram_api_base="${telegram_api_base%/}"
if [ "$VERIFY_TELEGRAM" = true ]; then
  printf '[pair-telegram] verifying Telegram bot token with getMe\n'
  BOT_INFO_JSON="$(python3 - "$telegram_api_base" "$BOT_TOKEN" <<'PY'
import json
import sys
from urllib import request

api_base, token = sys.argv[1:3]

def call(method, payload):
    req = request.Request(
        f"{api_base}/bot{token}/{method}",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    return json.loads(request.urlopen(req, timeout=15).read().decode("utf-8"))

try:
    data = call("getMe", {})
except Exception as exc:
    raise SystemExit(f"Telegram getMe failed: {exc}")
if not data.get("ok"):
    raise SystemExit(f"Telegram getMe rejected token: {data}")
result = data.get("result") or {}
if not result.get("is_bot"):
    raise SystemExit(f"Telegram getMe result is not a bot: {data}")
print(json.dumps({"id": result.get("id"), "username": result.get("username"), "first_name": result.get("first_name")}, ensure_ascii=False, sort_keys=True))
PY
)"
  if [ "$DELETE_WEBHOOK" = true ]; then
    printf '[pair-telegram] clearing Telegram webhook for long polling\n'
    python3 - "$telegram_api_base" "$BOT_TOKEN" <<'PY'
import json
import sys
from urllib import request

api_base, token = sys.argv[1:3]
req = request.Request(
    f"{api_base}/bot{token}/deleteWebhook",
    data=json.dumps({"drop_pending_updates": False}).encode("utf-8"),
    headers={"Content-Type": "application/json"},
    method="POST",
)
data = json.loads(request.urlopen(req, timeout=15).read().decode("utf-8"))
if not data.get("ok"):
    raise SystemExit(f"Telegram deleteWebhook failed: {data}")
PY
  else
    printf '[pair-telegram] Telegram webhook clearing skipped by --keep-webhook or PAIR_TELEGRAM_DELETE_WEBHOOK=false\n'
  fi
  if [ "$SYNC_COMMANDS_ON_STARTUP" = "1" ]; then
    printf '[pair-telegram] syncing Telegram command menu\n'
    python3 - "$telegram_api_base" "$BOT_TOKEN" <<'PY'
import json
import sys
from urllib import request

api_base, token = sys.argv[1:3]
commands = [
    {"command": "ai", "description": "运行 AI 或管理 runner"},
    {"command": "status", "description": "查看 runner 和 Codex 状态"},
    {"command": "help", "description": "显示 /ai 命令帮助"},
    {"command": "features", "description": "显示可用功能和 provider"},
    {"command": "codex", "description": "切换到 Codex 或直接让 Codex 执行任务"},
    {"command": "vscode", "description": "切换到 VSCode 或直接让 VSCode 执行任务"},
    {"command": "claude", "description": "切换到 Claude Code 或直接让 Claude Code 执行任务"},
    {"command": "gptmodel", "description": "切换 GPT 模型"},
    {"command": "claudemodel", "description": "切换 Claude 模型"},
    {"command": "shell", "description": "执行本机 shell 命令"},
]
req = request.Request(
    f"{api_base}/bot{token}/setMyCommands",
    data=json.dumps({"commands": commands}, ensure_ascii=False).encode("utf-8"),
    headers={"Content-Type": "application/json; charset=utf-8"},
    method="POST",
)
data = json.loads(request.urlopen(req, timeout=15).read().decode("utf-8"))
if not data.get("ok"):
    raise SystemExit(f"Telegram setMyCommands failed: {data}")
PY
  else
    printf '[pair-telegram] Telegram command menu sync skipped by --no-command-menu\n'
  fi
  if [ "$DISCOVER_CHAT_ID" = true ]; then
    printf '[pair-telegram] discovery mode: send any message to the bot within %ss\n' "$DISCOVER_TIMEOUT_SECONDS"
    DISCOVERED_CHAT_IDS="$(python3 - "$telegram_api_base" "$BOT_TOKEN" "$DISCOVER_TIMEOUT_SECONDS" <<'PY'
import json
import sys
import time
from urllib import request

api_base, token, timeout_raw = sys.argv[1:4]
try:
    timeout_seconds = max(0, int(float(timeout_raw)))
except ValueError:
    timeout_seconds = 45
deadline = time.time() + timeout_seconds
offset = None
seen = []

def call(method, payload, timeout):
    req = request.Request(
        f"{api_base}/bot{token}/{method}",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    data = json.loads(request.urlopen(req, timeout=timeout + 10).read().decode("utf-8"))
    if not data.get("ok"):
        raise SystemExit(f"Telegram {method} failed: {data}")
    return data

while True:
    remaining = max(0, int(deadline - time.time()))
    poll_timeout = min(10, remaining)
    payload = {"timeout": poll_timeout, "allowed_updates": ["message"]}
    if offset is not None:
        payload["offset"] = offset
    data = call("getUpdates", payload, poll_timeout)
    for update in data.get("result") or []:
        try:
            update_id = int(update.get("update_id", 0))
        except (TypeError, ValueError):
            update_id = 0
        offset = max(offset or 0, update_id + 1)
        message = update.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if chat_id is not None:
            value = str(chat_id)
            if value not in seen:
                seen.append(value)
    if seen or time.time() >= deadline or timeout_seconds == 0:
        break
print(",".join(seen))
PY
)"
    if [ -n "$DISCOVERED_CHAT_IDS" ]; then
      CHAT_IDS="$DISCOVERED_CHAT_IDS"
      DISCOVER_CHAT_ID=false
      printf '[pair-telegram] discovered Telegram chat_id(s): %s\n' "$CHAT_IDS"
    else
      printf '[pair-telegram] no chat_id discovered yet; leaving Telegram in discovery mode\n'
    fi
  fi
  if [ "$DISCOVER_CHAT_ID" != true ]; then
    printf '[pair-telegram] sending and editing Telegram pairing test message\n'
    python3 - "$telegram_api_base" "$BOT_TOKEN" "$CHAT_IDS" <<'PY'
import json
import sys
from urllib import request

api_base, token, chat_ids = sys.argv[1:4]
def call(method, payload):
    req = request.Request(
        f"{api_base}/bot{token}/{method}",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    data = json.loads(request.urlopen(req, timeout=15).read().decode("utf-8"))
    if not data.get("ok"):
        raise SystemExit(f"Telegram {method} failed: {data}")
    return data

for chat_id in [item for item in chat_ids.split(",") if item]:
    data = call(
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": "AI runner Telegram 配对测试成功。正在验证实时状态编辑。",
            "disable_web_page_preview": True,
        },
    )
    message = data.get("result") or {}
    message_id = message.get("message_id")
    if not isinstance(message_id, int):
        raise SystemExit(f"Telegram sendMessage did not return message_id for chat_id={chat_id}: {data}")
    call(
        "editMessageText",
        {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": "AI runner Telegram 配对测试成功。实时状态编辑已验证，可以发送 /ai 状态。",
            "disable_web_page_preview": True,
        },
    )
PY
  fi
else
  printf '[pair-telegram] Telegram API verification skipped by PAIR_TELEGRAM_VERIFY_API=false\n'
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
    $1 != "TELEGRAM_RESERVED_USD" &&
    $1 != "TELEGRAM_CLEAR_WEBHOOK_ON_STARTUP" &&
    $1 != "TELEGRAM_STATUS_INTERVAL_SECONDS" &&
    $1 != "TELEGRAM_STATUS_MIN_UPDATE_SECONDS" &&
    $1 != "TELEGRAM_SYNC_COMMANDS_ON_STARTUP" &&
    $1 != "TELEGRAM_ALLOWED_UPDATES" &&
    $1 != "TELEGRAM_NATIVE_DRAFT_PROGRESS" &&
    $1 != "TELEGRAM_NATIVE_DRAFT_ALLOW_CHAT_IDS" &&
    $1 != "TELEGRAM_GROUP_MODE"
  ' "$STATE_ROOT/config.env" > "$TMP_CONFIG"
  sudo cp "$TMP_CONFIG" "$STATE_ROOT/config.env"
  rm -f "$TMP_CONFIG"
fi
sudo tee -a "$STATE_ROOT/config.env" >/dev/null <<EOF
TELEGRAM_BOT_TOKEN=$BOT_TOKEN
TELEGRAM_ALLOWED_CHAT_IDS=$CHAT_IDS
TELEGRAM_CLEAR_WEBHOOK_ON_STARTUP=$([ "$DELETE_WEBHOOK" = true ] && printf '1' || printf '0')
TELEGRAM_STATUS_INTERVAL_SECONDS=$STATUS_INTERVAL_SECONDS
TELEGRAM_STATUS_MIN_UPDATE_SECONDS=$STATUS_MIN_UPDATE_SECONDS
TELEGRAM_SYNC_COMMANDS_ON_STARTUP=$SYNC_COMMANDS_ON_STARTUP
TELEGRAM_ALLOWED_UPDATES=$ALLOWED_UPDATES
TELEGRAM_NATIVE_DRAFT_PROGRESS=$NATIVE_DRAFT_PROGRESS
TELEGRAM_GROUP_MODE=$GROUP_MODE
EOF
if [ -n "$NATIVE_DRAFT_ALLOW_CHAT_IDS" ]; then
  printf 'TELEGRAM_NATIVE_DRAFT_ALLOW_CHAT_IDS=%s\n' "$NATIVE_DRAFT_ALLOW_CHAT_IDS" | sudo tee -a "$STATE_ROOT/config.env" >/dev/null
fi
if [ -n "$API_BASE" ]; then
  printf 'TELEGRAM_API_BASE=%s\n' "$API_BASE" | sudo tee -a "$STATE_ROOT/config.env" >/dev/null
fi
if [ -n "$RESERVED_USD" ]; then
  printf 'TELEGRAM_RESERVED_USD=%s\n' "$NORMALIZED_RESERVED_USD" | sudo tee -a "$STATE_ROOT/config.env" >/dev/null
fi
sudo chmod 0600 "$STATE_ROOT/config.env"

printf '[pair-telegram] Telegram pairing config written\n'
if [ -f "$STATE_ROOT/install-manifest.json" ]; then
  sudo env STATE_ROOT="$STATE_ROOT" DISCOVER_CHAT_ID="$DISCOVER_CHAT_ID" BOT_INFO_JSON="$BOT_INFO_JSON" TELEGRAM_CHAT_IDS="$CHAT_IDS" TELEGRAM_API_BASE_EFFECTIVE="$telegram_api_base" DELETE_WEBHOOK="$DELETE_WEBHOOK" STATUS_INTERVAL_SECONDS="$STATUS_INTERVAL_SECONDS" STATUS_MIN_UPDATE_SECONDS="$STATUS_MIN_UPDATE_SECONDS" SYNC_COMMANDS_ON_STARTUP="$SYNC_COMMANDS_ON_STARTUP" ALLOWED_UPDATES="$ALLOWED_UPDATES" NATIVE_DRAFT_PROGRESS="$NATIVE_DRAFT_PROGRESS" NATIVE_DRAFT_ALLOW_CHAT_IDS="$NATIVE_DRAFT_ALLOW_CHAT_IDS" GROUP_MODE="$GROUP_MODE" VERIFY_TELEGRAM="$VERIFY_TELEGRAM" RESERVED_USD="$RESERVED_USD" NORMALIZED_RESERVED_USD="$NORMALIZED_RESERVED_USD" python3 - <<'PY'
import json
import os
from pathlib import Path

path = Path(os.environ["STATE_ROOT"]) / "install-manifest.json"
data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
data["telegram_enabled"] = True
data["telegram_status"] = "discovery" if os.environ.get("DISCOVER_CHAT_ID") == "true" else "paired"
data["telegram_paired"] = os.environ.get("DISCOVER_CHAT_ID") != "true"
data["telegram_allowed_chat_ids"] = [item for item in os.environ.get("TELEGRAM_CHAT_IDS", "").split(",") if item]
data["telegram_api_base"] = os.environ.get("TELEGRAM_API_BASE_EFFECTIVE", "")
data["telegram_webhook_cleared_for_polling"] = os.environ.get("DELETE_WEBHOOK") == "true"
data["telegram_status_interval_seconds"] = os.environ.get("STATUS_INTERVAL_SECONDS", "")
data["telegram_status_min_update_seconds"] = os.environ.get("STATUS_MIN_UPDATE_SECONDS", "")
data["telegram_sync_commands_on_startup"] = os.environ.get("SYNC_COMMANDS_ON_STARTUP") == "1"
data["telegram_commands_synced_at_pairing"] = os.environ.get("VERIFY_TELEGRAM") == "true" and os.environ.get("SYNC_COMMANDS_ON_STARTUP") == "1"
data["telegram_allowed_updates"] = [item for item in os.environ.get("ALLOWED_UPDATES", "").split(",") if item]
data["telegram_edit_status_verified"] = os.environ.get("VERIFY_TELEGRAM") == "true" and os.environ.get("DISCOVER_CHAT_ID") != "true"
data["telegram_native_draft_progress"] = os.environ.get("NATIVE_DRAFT_PROGRESS") == "1"
data["telegram_native_draft_allow_chat_ids"] = [item for item in os.environ.get("NATIVE_DRAFT_ALLOW_CHAT_IDS", "").split(",") if item]
data["telegram_group_mode"] = os.environ.get("GROUP_MODE", "mention")
if os.environ.get("RESERVED_USD", ""):
    data["telegram_reserved_usd_input"] = os.environ.get("RESERVED_USD", "")
    data["telegram_reserved_usd"] = os.environ.get("NORMALIZED_RESERVED_USD", "")
try:
    bot_info = json.loads(os.environ.get("BOT_INFO_JSON") or "{}")
except json.JSONDecodeError:
    bot_info = {}
if bot_info:
    data["telegram_bot"] = {key: value for key, value in bot_info.items() if value is not None}
path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
  sudo chmod 0600 "$STATE_ROOT/install-manifest.json"
fi
if [ "$DISCOVER_CHAT_ID" = true ]; then
  printf '[pair-telegram] discovery mode enabled; send any message to the bot and it will reply with chat_id without running AI commands\n'
fi
if command -v systemctl >/dev/null 2>&1 && [ -f "$TELEGRAM_SYSTEMD_UNIT" ]; then
  sudo systemctl daemon-reload
  restart_or_defer_telegram_service
  printf '[pair-telegram] ai-telegram-bot.service enabled\n'
else
  printf '[pair-telegram] systemd service not found; run /opt/ai-remote-runner/run-telegram-local.sh manually on non-systemd installs\n'
fi

if [ "${PAIR_TELEGRAM_VALIDATE_CORE_READY:-true}" = true ] && { [ -x "$VALIDATE_CORE_READY_SCRIPT" ] || [ -f "$VALIDATE_CORE_READY_SCRIPT" ]; }; then
  printf '[pair-telegram] running core readiness validation for Telegram channel\n'
  sudo env AI_REMOTE_STATE="$STATE_ROOT" bash "$VALIDATE_CORE_READY_SCRIPT"
else
  printf '[pair-telegram] core readiness validation skipped; run validate-core-ready.sh before treating Telegram as ready\n'
fi
