#!/usr/bin/env bash
# Apply Claude Code stability settings without restarting active services.

set -euo pipefail

STATE_ROOT="${AI_REMOTE_STATE:-${AI_STATE_ROOT:-/var/lib/ai-remote-runner}}"
CONFIG_ENV="$STATE_ROOT/config.env"
PENDING_FILE="$STATE_ROOT/pending-service-restart.txt"

log() {
  printf '[fix-stability] %s\n' "$*"
}

inside_systemd_unit() {
  local unit="$1"
  grep -q -- "$unit" "/proc/$$/cgroup" 2>/dev/null
}

write_pending_restart() {
  local timestamp
  timestamp="$(date -Is)"
  mkdir -p "$STATE_ROOT"
  if [ -s "$PENDING_FILE" ] && grep -q '^unit=ai-telegram-bot.service ai-remote-runner.service$' "$PENDING_FILE"; then
    return 0
  fi
  {
    printf '[restart:%s]\n' "$timestamp"
    printf 'unit=%s\n' 'ai-telegram-bot.service ai-remote-runner.service'
    printf 'reason=%s\n' 'Claude stability settings changed; restart after the active task finishes'
    printf 'created_at=%s\n' "$timestamp"
    printf 'command=%s\n' 'sudo systemctl restart ai-telegram-bot ai-remote-runner'
    printf '\n'
  } >> "$PENDING_FILE"
  chmod 0600 "$PENDING_FILE"
}

apply_config_updates() {
  CONFIG_ENV="$CONFIG_ENV" python3 <<'PY'
import os
from pathlib import Path

path = Path(os.environ["CONFIG_ENV"])
path.parent.mkdir(parents=True, exist_ok=True)
lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
updates = {
    "CLAUDE_MAX_TURNS": "50",
    "AI_TASK_TIMEOUT_SECONDS": "3600",
    "CLAUDE_API_RETRY_ATTEMPTS": "8",
    "CLAUDE_API_RETRY_SLEEP_SECONDS": "5",
    "VSCODE_CLAUDE_MAX_TURNS": "50",
    "VSCODE_CLAUDE_API_RETRY_ATTEMPTS": "8",
    "VSCODE_CLAUDE_API_RETRY_SLEEP_SECONDS": "5",
    "AI_LOCAL_EXEC_TIMEOUT_SECONDS": "600",
    "TELEGRAM_SHUTDOWN_DRAIN_SECONDS": "3600",
    "TELEGRAM_STATUS_INTERVAL_SECONDS": "5",
    "TELEGRAM_STATUS_MIN_UPDATE_SECONDS": "0.8",
    "AI_PROCESS_CONTROL_ENABLED": "1",
}
seen = set()
out = []
for line in lines:
    if not line or line.lstrip().startswith("#") or "=" not in line:
        out.append(line)
        continue
    key, _ = line.split("=", 1)
    if key in updates:
        out.append(f"{key}={updates[key]}")
        seen.add(key)
    else:
        out.append(line)
for key, value in updates.items():
    if key not in seen:
        out.append(f"{key}={value}")
path.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
path.chmod(0o600)
PY
}

main() {
  log "config=$CONFIG_ENV"
  apply_config_updates
  write_pending_restart
  log "stability settings written"
  if inside_systemd_unit "ai-telegram-bot.service"; then
    log "running inside ai-telegram-bot.service; restart deferred to avoid SIGTERM/returncode=143 deadlock"
  fi
  log "no services were restarted"
  log "after active tasks finish, apply with: sudo systemctl restart ai-telegram-bot ai-remote-runner"
}

main "$@"
