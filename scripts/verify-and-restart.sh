#!/usr/bin/env bash
# Verify runtime/config differences and defer service restarts.

set -euo pipefail

STATE_ROOT="${AI_REMOTE_STATE:-/var/lib/ai-remote-runner}"
CONFIG_ENV="$STATE_ROOT/config.env"
PENDING_FILE="$STATE_ROOT/pending-service-restart.txt"

log() {
  printf '[verify-restart] %s\n' "$*"
}

config_value() {
  local key="$1"
  grep "^${key}=" "$CONFIG_ENV" 2>/dev/null | tail -1 | cut -d= -f2- || true
}

running_value() {
  local key="$1"
  local pid
  pid="$(pgrep -f 'ai_remote_runner.cli telegram' | head -1 || true)"
  if [ -z "$pid" ]; then
    return 0
  fi
  ps eww "$pid" 2>/dev/null | tr ' ' '\n' | grep "^${key}=" | tail -1 | cut -d= -f2- || true
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
    printf 'reason=%s\n' 'Runtime environment differs from config.env; restart after active tasks finish'
    printf 'created_at=%s\n' "$timestamp"
    printf 'command=%s\n' 'sudo systemctl restart ai-telegram-bot ai-remote-runner'
    printf '\n'
  } >> "$PENDING_FILE"
  chmod 0600 "$PENDING_FILE"
}

main() {
  if [ ! -f "$CONFIG_ENV" ]; then
    log "missing config: $CONFIG_ENV"
    exit 1
  fi

  local needs_restart=false
  for key in CLAUDE_MAX_TURNS AI_TASK_TIMEOUT_SECONDS AI_LOCAL_EXEC_TIMEOUT_SECONDS CLAUDE_API_RETRY_ATTEMPTS; do
    local cfg run
    cfg="$(config_value "$key")"
    run="$(running_value "$key")"
    printf '%s config=%s running=%s\n' "$key" "${cfg:-unset}" "${run:-unset}"
    if [ -n "$cfg" ] && [ -n "$run" ] && [ "$cfg" != "$run" ]; then
      needs_restart=true
    fi
  done

  if [ "$needs_restart" = true ]; then
    write_pending_restart
    log "restart is needed, but no service was restarted"
    log "after active tasks finish, apply with: sudo systemctl restart ai-telegram-bot ai-remote-runner"
  else
    log "runtime configuration already matches config.env or service is not running"
  fi
}

main "$@"
