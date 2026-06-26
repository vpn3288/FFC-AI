#!/usr/bin/env bash
# Apply conservative stability settings without restarting active services.

set -euo pipefail

STATE_ROOT="${AI_REMOTE_STATE:-${AI_STATE_ROOT:-/var/lib/ai-remote-runner}}"
CONFIG_ENV="$STATE_ROOT/config.env"

log() {
  printf '[optimize-stability] %s\n' "$*"
}

write_env_updates() {
  CONFIG_ENV="$CONFIG_ENV" python3 <<'PY'
import os
from pathlib import Path

path = Path(os.environ["CONFIG_ENV"])
path.parent.mkdir(parents=True, exist_ok=True)
lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
updates = {
    "AI_TASK_TIMEOUT_SECONDS": "7200",
    "TELEGRAM_SHUTDOWN_DRAIN_SECONDS": "7200",
    "CLAUDE_API_RETRY_ATTEMPTS": "5",
    "CLAUDE_API_RETRY_SLEEP_SECONDS": "5",
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

install_readonly_health_check_hint() {
  if [ "${FFC_AI_INSTALL_HEALTH_CRON:-0}" != "1" ]; then
    log "health cron not installed by default; run scripts/health-check.sh manually or set FFC_AI_INSTALL_HEALTH_CRON=1"
    return 0
  fi
  local script_path
  script_path="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/health-check.sh"
  if ! crontab -l 2>/dev/null | grep -q "$script_path"; then
    (crontab -l 2>/dev/null; printf '*/15 * * * * AI_REMOTE_STATE=%s bash %s\n' "$STATE_ROOT" "$script_path") | crontab -
    log "installed read-only health check cron every 15 minutes"
  else
    log "read-only health check cron already installed"
  fi
}

main() {
  log "state_root=$STATE_ROOT"
  write_env_updates
  log "updated conservative timeout/retry/status settings in $CONFIG_ENV"
  install_readonly_health_check_hint
  log "no services were restarted"
  log "after active AI tasks finish, apply changes with: sudo systemctl restart ai-telegram-bot ai-remote-runner"
}

main "$@"
