#!/usr/bin/env bash
# Read-only health check for FFC-AI runner installs.

set -euo pipefail

STATE_ROOT="${AI_REMOTE_STATE:-/var/lib/ai-remote-runner}"
LOG_FILE="${FFC_AI_HEALTH_LOG:-$STATE_ROOT/health-check.log}"
TIMESTAMP="$(date '+%Y-%m-%d %H:%M:%S')"

log() {
  printf '[health-check] %s\n' "$*"
  mkdir -p "$(dirname "$LOG_FILE")" 2>/dev/null || true
  printf '[%s] %s\n' "$TIMESTAMP" "$*" >> "$LOG_FILE" 2>/dev/null || true
}

service_state() {
  local unit="$1"
  if command -v systemctl >/dev/null 2>&1; then
    local state
    state="$(systemctl is-active "$unit" 2>/dev/null || true)"
    if [ -n "$state" ]; then
      printf '%s' "$state"
    else
      printf 'unknown'
    fi
  else
    printf 'no-systemd'
  fi
}

json_count_active_processes() {
  python3 - "$STATE_ROOT/active-processes.json" <<'PY' 2>/dev/null || printf '0'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
if not path.exists():
    print(0)
    raise SystemExit
data = json.loads(path.read_text(encoding="utf-8") or "{}")
print(len(data) if isinstance(data, dict) else 0)
PY
}

log "starting read-only health check"
log "state_root=$STATE_ROOT"
log "ai-telegram-bot=$(service_state ai-telegram-bot.service)"
log "ai-remote-runner=$(service_state ai-remote-runner.service)"
log "active_registered_processes=$(json_count_active_processes)"
claude_process_count="$(pgrep -fc '(^|/)claude( |$)' 2>/dev/null || true)"
log "claude_processes=${claude_process_count:-0}"

if [ -f "$STATE_ROOT/events.jsonl" ]; then
  log "recent_error_events=$(tail -100 "$STATE_ROOT/events.jsonl" | grep -c '\"phase\"[[:space:]]*:[[:space:]]*\"error\"' || true)"
fi

if command -v free >/dev/null 2>&1; then
  log "memory=$(free -h | awk 'NR==2{print \"used=\" $3 \", available=\" $7}')"
fi

if command -v df >/dev/null 2>&1; then
  log "disk=$(df -h "$STATE_ROOT" 2>/dev/null | awk 'NR==2{print \"used=\" $5 \", avail=\" $4}')"
fi

if command -v systemctl >/dev/null 2>&1; then
  log "recent_service_sigterm_events=$(journalctl -u ai-telegram-bot --since '1 hour ago' --no-pager 2>/dev/null | grep -c 'SIGTERM\\|Sending signal' || true)"
fi

log "completed read-only health check"
