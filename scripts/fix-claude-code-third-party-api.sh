#!/usr/bin/env bash
# Repair Claude Code third-party API settings for the root runner.

set -euo pipefail

CONFIG_FILE="${AI_REMOTE_CONFIG:-/var/lib/ai-remote-runner/config.env}"
AI_TOOL_HOME="${AI_TOOL_HOME:-/root}"
CLAUDE_SETTINGS="${CLAUDE_SETTINGS:-$AI_TOOL_HOME/.claude/settings.json}"

log() {
  printf '[fix-claude-third-party] %s\n' "$*"
}

fail() {
  log "ERROR: $*"
  exit 1
}

[ -f "$CONFIG_FILE" ] || fail "config file not found: $CONFIG_FILE"

set -a
# shellcheck disable=SC1090
. "$CONFIG_FILE"
set +a

BASE_URL="${ANTHROPIC_BASE_URL:-${ANTHROPIC_API_URL:-}}"
API_KEY="${ANTHROPIC_AUTH_TOKEN:-${ANTHROPIC_API_KEY:-}}"

[ -n "$BASE_URL" ] || fail "ANTHROPIC_BASE_URL or ANTHROPIC_API_URL is required"
[ -n "$API_KEY" ] || fail "ANTHROPIC_AUTH_TOKEN or ANTHROPIC_API_KEY is required"

BASE_URL="${BASE_URL%/}"
API_URL="${ANTHROPIC_API_URL:-$BASE_URL}"

log "config: $CONFIG_FILE"
log "claude settings: $CLAUDE_SETTINGS"
log "base url: $BASE_URL"
log "key: ${API_KEY:0:6}...${API_KEY: -4}"

CONFIG_FILE="$CONFIG_FILE" \
CLAUDE_SETTINGS="$CLAUDE_SETTINGS" \
ANTHROPIC_BASE_URL="$BASE_URL" \
ANTHROPIC_API_URL="$API_URL" \
ANTHROPIC_AUTH_TOKEN="$API_KEY" \
ANTHROPIC_API_KEY="$API_KEY" \
CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="${CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC:-1}" \
python3 - <<'PY'
import json
import os
from pathlib import Path


def parse_env(path: Path) -> tuple[list[str], dict[str, str]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    data: dict[str, str] = {}
    for line in lines:
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key] = value
    return lines, data


def write_env(path: Path, updates: dict[str, str]) -> None:
    lines, data = parse_env(path)
    data.update({key: value for key, value in updates.items() if value})
    ordered = []
    seen = set()
    for line in lines:
        if not line or line.lstrip().startswith("#") or "=" not in line:
            ordered.append(line)
            continue
        key, _ = line.split("=", 1)
        if key in data:
            ordered.append(f"{key}={data[key]}")
            seen.add(key)
        else:
            ordered.append(line)
    for key in updates:
        if key in data and key not in seen:
            ordered.append(f"{key}={data[key]}")
    path.write_text("\n".join(ordered).rstrip() + "\n", encoding="utf-8")
    path.chmod(0o600)


config = Path(os.environ["CONFIG_FILE"])
settings = Path(os.environ["CLAUDE_SETTINGS"])

base_url = os.environ["ANTHROPIC_BASE_URL"].rstrip("/")
api_url = (os.environ.get("ANTHROPIC_API_URL") or base_url).rstrip("/")
api_key = os.environ["ANTHROPIC_AUTH_TOKEN"]

updates = {
    "ANTHROPIC_BASE_URL": base_url,
    "ANTHROPIC_API_URL": api_url,
    "ANTHROPIC_AUTH_TOKEN": api_key,
    "ANTHROPIC_API_KEY": api_key,
    "CLAUDE_CODE_DISABLE_OAUTH": "1",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": os.environ.get("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", "1"),
    "AI_TASK_TIMEOUT_SECONDS": "7200",
    "CLAUDE_API_RETRY_ATTEMPTS": "5",
    "CLAUDE_API_RETRY_SLEEP_SECONDS": "5",
}
write_env(config, updates)

settings.parent.mkdir(parents=True, exist_ok=True)
try:
    data = json.loads(settings.read_text(encoding="utf-8")) if settings.exists() else {}
except json.JSONDecodeError:
    data = {}
env = data.get("env") if isinstance(data.get("env"), dict) else {}
env = {str(k): str(v) for k, v in env.items()}
for key in (
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_API_URL",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_API_KEY",
    "CLAUDE_CODE_DISABLE_OAUTH",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC",
):
    env[key] = updates[key]
data["env"] = env
data["thirdPartyApi"] = True
data["requestTimeout"] = 180000
data["streamTimeout"] = 600000
data["maxRetries"] = 5
settings.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
settings.chmod(0o600)
PY

endpoint_candidates() {
  local base="${1%/}"
  if [[ "$base" == */v1 ]]; then
    printf '%s/messages\n' "$base"
  else
    printf '%s/v1/messages\n' "$base"
    printf '%s/messages\n' "$base"
  fi
}

if command -v curl >/dev/null 2>&1; then
  log "testing Claude-compatible endpoint"
  ok=false
  while IFS= read -r endpoint; do
    [ -n "$endpoint" ] || continue
    code="$(
      curl -sS -o /dev/null -w '%{http_code}' -m 20 \
        -H "x-api-key: $API_KEY" \
        -H "Authorization: Bearer $API_KEY" \
        -H 'anthropic-version: 2023-06-01' \
        -H 'Content-Type: application/json' \
        -X POST "$endpoint" \
        -d '{"model":"claude-sonnet-4-5","max_tokens":1,"messages":[{"role":"user","content":"ping"}]}' \
        2>/dev/null || printf '000'
    )"
    log "$endpoint -> HTTP $code"
    case "$code" in
      200|400|401|403|404|429)
        ok=true
        break
        ;;
    esac
  done < <(endpoint_candidates "$BASE_URL")
  if [ "$ok" != true ]; then
    log "WARNING: endpoint test did not get an HTTP response from a known Claude-compatible path"
  fi
else
  log "curl not found; skipped endpoint test"
fi

log "updated config.env and Claude settings"
log "restart with: systemctl restart ai-telegram-bot ai-remote-runner"
