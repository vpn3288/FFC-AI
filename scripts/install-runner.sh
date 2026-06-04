#!/usr/bin/env bash
set -euo pipefail

DRY_RUN=false
STATE_ROOT="${AI_REMOTE_STATE:-/var/lib/ai-remote-runner}"
WORKSPACE_ROOT="${AI_WORKSPACE_ROOT:-/srv/ai-workspaces}"
INSTALL_ROOT="${AI_REMOTE_INSTALL_ROOT:-/opt/ai-remote-runner}"
CLAUDE_MODEL="${CLAUDE_MODEL:-claude-opus-4-6-20260130}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
export PYTHONPATH="${PYTHONPATH:-$REPO_ROOT/src}"

usage() {
  printf 'usage: %s [--dry-run]\n' "$0"
}

log() {
  printf '[install-runner] %s\n' "$*"
}

run() {
  if [ "$DRY_RUN" = true ]; then
    printf '[dry-run] %s\n' "$*"
  else
    "$@"
  fi
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=true ;;
    -h|--help) usage; exit 0 ;;
    *) usage; exit 2 ;;
  esac
  shift
done

log 'stage 01: detect OS, WSL, architecture, systemd availability, shell, PATH'
OS_ID="$(. /etc/os-release 2>/dev/null; printf '%s' "${ID:-unknown}")"
ARCH="$(uname -m)"
SYSTEMD=false
if command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ]; then
  SYSTEMD=true
fi
WSL=false
if grep -qi microsoft /proc/version 2>/dev/null; then
  WSL=true
fi

log "detected os=$OS_ID arch=$ARCH systemd=$SYSTEMD wsl=$WSL"

log 'stage 02: install system packages required by runner'
if command -v apt-get >/dev/null 2>&1; then
  run sudo apt-get update
  run sudo apt-get install -y python3 python3-pip ca-certificates curl git openssl gpg
else
  log 'apt-get unavailable; package installation skipped and must be handled externally'
fi

log 'stage 03: install or verify Claude Code'
if command -v claude >/dev/null 2>&1; then
  claude --version
elif command -v apt-get >/dev/null 2>&1; then
  run sudo install -d -m 0755 /etc/apt/keyrings
  run sudo curl -fsSL https://downloads.claude.ai/keys/claude-code.asc -o /etc/apt/keyrings/claude-code.asc
  if [ "$DRY_RUN" = false ]; then
    if command -v gpg >/dev/null 2>&1; then
      gpg --show-keys /etc/apt/keyrings/claude-code.asc | grep -q '31DD DE24 DDFA B679 F42D  7BD2 BAA9 29FF 1A7E CACE' || {
        log 'Claude Code apt signing key fingerprint mismatch'
        exit 1
      }
    fi
    echo "deb [signed-by=/etc/apt/keyrings/claude-code.asc] https://downloads.claude.ai/claude-code/apt/stable stable main" \
      | sudo tee /etc/apt/sources.list.d/claude-code.list >/dev/null
  else
    log 'would verify Claude Code apt key fingerprint and write /etc/apt/sources.list.d/claude-code.list'
  fi
  run sudo apt-get update
  run sudo apt-get install -y claude-code
  command -v claude >/dev/null 2>&1 || { log 'claude install failed; see official Claude Code installation docs'; exit 1; }
  claude --version
else
  log 'claude missing and native installer unavailable; install from official Claude Code docs before core_ready'
  exit 1
fi

log 'stage 04: install or verify Codex CLI'
if command -v codex >/dev/null 2>&1; then
  codex --version
else
  log 'codex missing; codex_status=external_prerequisite'
fi

log 'stage 05: create runner directories'
run sudo mkdir -p "$STATE_ROOT"/{credentials,instructions/snapshots,budget} "$WORKSPACE_ROOT" "$INSTALL_ROOT"
run sudo cp -R "$REPO_ROOT"/src "$INSTALL_ROOT"/
run sudo cp "$REPO_ROOT"/pyproject.toml "$INSTALL_ROOT"/
if [ "$DRY_RUN" = false ]; then
  (cd "$INSTALL_ROOT" && sudo python3 -m pip install --break-system-packages -e .)
else
  log "would install Python package from $INSTALL_ROOT"
fi

log 'stage 06: create runner configuration files'
if [ "$DRY_RUN" = false ]; then
  sudo tee "$STATE_ROOT/config.env" >/dev/null <<EOF
AI_REMOTE_STATE=$STATE_ROOT
AI_WORKSPACE_ROOT=$WORKSPACE_ROOT
CLAUDE_MODEL=$CLAUDE_MODEL
CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=\${CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC:-1}
AI_BRIDGE_SHARED_SECRET=\${AI_BRIDGE_SHARED_SECRET:-}
EOF
  sudo chmod 0600 "$STATE_ROOT/config.env"
else
  log "would write $STATE_ROOT/config.env"
fi

log 'stage 07: create credential broker storage backend'
run sudo chmod 0700 "$STATE_ROOT/credentials"

log 'stage 08: install runner bridge service'
if [ "$SYSTEMD" = true ]; then
  if [ "$DRY_RUN" = false ]; then
    sudo tee /etc/systemd/system/ai-remote-runner.service >/dev/null <<EOF
[Unit]
Description=AI Remote Runner Bridge
After=network-online.target

[Service]
Type=simple
EnvironmentFile=$STATE_ROOT/config.env
WorkingDirectory=$INSTALL_ROOT
ExecStart=/usr/bin/python3 -m ai_remote_runner.cli bridge --host 127.0.0.1 --port 8765
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
    sudo systemctl daemon-reload
    sudo systemctl enable ai-remote-runner.service
    if [ -n "${AI_BRIDGE_SHARED_SECRET:-}" ]; then
      sudo systemctl start ai-remote-runner.service
    else
      log 'AI_BRIDGE_SHARED_SECRET is empty; service installed/enabled but not started until pairing'
    fi
  else
    log 'would install /etc/systemd/system/ai-remote-runner.service'
    log 'would enable/start ai-remote-runner.service after bridge secret is configured'
  fi
else
  if [ "$DRY_RUN" = false ]; then
    sudo tee "$INSTALL_ROOT/run-local.sh" >/dev/null <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
source "${AI_REMOTE_STATE:-/var/lib/ai-remote-runner}/config.env"
exec python3 -m ai_remote_runner.cli bridge --host 127.0.0.1 --port 8765
EOF
    sudo chmod +x "$INSTALL_ROOT/run-local.sh"
  else
    log "would write $INSTALL_ROOT/run-local.sh"
  fi
fi

log 'stage 09: connect runner to communication platform'
log 'use scripts/pair-runner.sh with Mattermost URL, webhook URL, bot token, and bridge shared secret'

log 'stage 10: run provider smoke tests'
python3 -m ai_remote_runner.cli providers
if [ "$DRY_RUN" = false ] && command -v claude >/dev/null 2>&1; then
  claude auth status --json >/dev/null || log 'claude auth/API config pending'
  claude -p --bare --output-format json --max-turns 1 --max-budget-usd 0.05 --tools "" --no-session-persistence -- 'Return OK only.' >/dev/null || log 'claude print-json smoke pending'
elif [ "$DRY_RUN" = true ]; then
  log 'would run claude auth status and print-json smoke test'
fi

log 'stage 11: run phone command smoke tests'
python3 -m ai_remote_runner.cli parse '/ai 状态'
python3 -m ai_remote_runner.cli index >/dev/null

log 'stage 12: report core_ready or failed'
if [ "$DRY_RUN" = false ]; then
  sudo tee "$STATE_ROOT/install-manifest.json" >/dev/null <<EOF
{
  "component": "ai-remote-runner",
  "state_root": "$STATE_ROOT",
  "workspace_root": "$WORKSPACE_ROOT",
  "install_root": "$INSTALL_ROOT",
  "systemd": $SYSTEMD,
  "wsl": $WSL,
  "claude_model": "$CLAUDE_MODEL",
  "created_files": [
    "$STATE_ROOT/config.env",
    "$STATE_ROOT/install-manifest.json"
  ],
  "created_dirs": [
    "$STATE_ROOT",
    "$WORKSPACE_ROOT",
    "$INSTALL_ROOT"
  ]
}
EOF
  sudo chmod 0600 "$STATE_ROOT/install-manifest.json"
else
  log "would write $STATE_ROOT/install-manifest.json"
fi
log 'core_ready=false until bridge pairing, credential test, and phone loopback pass'
if [ "$DRY_RUN" = false ] && [ -n "${AI_BRIDGE_SHARED_SECRET:-}" ] && [ -n "${MATTERMOST_WEBHOOK_URL:-}" ]; then
  "$SCRIPT_DIR/validate-core-ready.sh" || log 'core_ready validation failed'
else
  log 'skip core_ready validation until pairing supplies bridge secret and Mattermost webhook'
fi
