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

read_lock() {
  grep "^$1=" "$REPO_ROOT/versions.lock" | cut -d= -f2-
}

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

apt_install() {
  if [ "$DRY_RUN" = true ]; then
    run sudo apt-get install -y "$@"
  else
    sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y "$@"
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
  apt_install python3 python3-pip ca-certificates curl git openssl gpg
else
  log 'apt-get unavailable; package installation skipped and must be handled externally'
fi

log 'stage 03: install or verify Claude Code'
if command -v claude >/dev/null 2>&1; then
  claude --version
elif command -v apt-get >/dev/null 2>&1; then
  # Source: official Claude Code installation docs, https://code.claude.com/docs/en/installation
  run sudo install -d -m 0755 /etc/apt/keyrings
  run sudo curl -fsSL https://downloads.claude.ai/keys/claude-code.asc -o /etc/apt/keyrings/claude-code.asc
  if [ "$DRY_RUN" = false ]; then
    command -v gpg >/dev/null 2>&1 || { log 'gpg required for Claude Code apt key verification'; exit 1; }
    ACTUAL_FINGERPRINT="$(gpg --show-keys --with-colons /etc/apt/keyrings/claude-code.asc | awk -F: '/^fpr:/ {print $10; exit}')"
    EXPECTED_FINGERPRINT="$(read_lock claude_code_apt_key_fingerprint)"
    [ -n "$EXPECTED_FINGERPRINT" ] || { log 'versions.lock must pin claude_code_apt_key_fingerprint'; exit 1; }
    [ "$ACTUAL_FINGERPRINT" = "$EXPECTED_FINGERPRINT" ] || {
      log 'Claude Code apt signing key fingerprint mismatch'
      exit 1
    }
    echo "deb [signed-by=/etc/apt/keyrings/claude-code.asc] https://downloads.claude.ai/claude-code/apt/stable stable main" \
      | sudo tee /etc/apt/sources.list.d/claude-code.list >/dev/null
  else
    log 'would verify Claude Code apt key fingerprint and write /etc/apt/sources.list.d/claude-code.list'
  fi
  run sudo apt-get update
  apt_install claude-code
  command -v claude >/dev/null 2>&1 || { log 'claude install failed; see official Claude Code installation docs'; exit 1; }
  claude --version
else
  log 'claude missing and native installer unavailable; install from official Claude Code docs before core_ready'
  exit 1
fi

log 'stage 04: install or verify Codex CLI'
CODEX_NPM_PACKAGE="$(read_lock codex_npm_package || true)"
CODEX_NPM_VERSION="$(read_lock codex_npm_version || true)"
CODEX_NPM_PACKAGE="${CODEX_NPM_PACKAGE:-@openai/codex}"
CODEX_NPM_VERSION="${CODEX_NPM_VERSION:-0.137.0}"
CODEX_READY=false
CODEX_STATUS="external_prerequisite"
CODEX_REMEDIATION_ZH="Codex CLI 未安装；请按当前 Codex 官方安装说明手动安装后重新运行 /ai 提供商 列表。"
if command -v codex >/dev/null 2>&1; then
  codex --version
  CODEX_READY=true
  CODEX_STATUS="installed"
  CODEX_REMEDIATION_ZH=""
elif command -v apt-get >/dev/null 2>&1; then
  log "codex missing; installing $CODEX_NPM_PACKAGE@$CODEX_NPM_VERSION through npm"
  apt_install nodejs npm
  if [ "$DRY_RUN" = false ]; then
    npm install -g "$CODEX_NPM_PACKAGE@$CODEX_NPM_VERSION"
    command -v codex >/dev/null 2>&1 || { log 'codex npm install did not place codex on PATH'; exit 1; }
    codex --version
    CODEX_READY=true
    CODEX_STATUS="installed"
    CODEX_REMEDIATION_ZH=""
  else
    log "would run npm install -g $CODEX_NPM_PACKAGE@$CODEX_NPM_VERSION"
  fi
else
  log 'codex missing; codex_status=external_prerequisite'
fi
if [ -n "${CODEX_API_KEY:-}" ] || [ -n "${OPENAI_API_KEY:-}" ]; then
  CODEX_KEY="${CODEX_API_KEY:-$OPENAI_API_KEY}"
  if [ "$DRY_RUN" = false ]; then
    mkdir -p "$HOME/.codex"
    cat > "$HOME/.codex/config.toml" <<EOF
model_provider = "${CODEX_MODEL_PROVIDER:-OpenAI}"
model = "${CODEX_MODEL:-gpt-5.5}"
review_model = "${CODEX_REVIEW_MODEL:-${CODEX_MODEL:-gpt-5.5}}"
model_reasoning_effort = "${CODEX_REASONING_EFFORT:-xhigh}"
disable_response_storage = true
network_access = "enabled"
model_context_window = ${CODEX_CONTEXT_WINDOW:-200000}
model_auto_compact_token_limit = ${CODEX_AUTO_COMPACT_TOKEN_LIMIT:-160000}

[model_providers.${CODEX_MODEL_PROVIDER:-OpenAI}]
name = "${CODEX_MODEL_PROVIDER:-OpenAI}"
base_url = "${CODEX_BASE_URL:-https://api.openai.com/v1}"
wire_api = "responses"
requires_openai_auth = true

[features]
goals = true
EOF
    CODEX_KEY="$CODEX_KEY" python3 - <<'PY'
import json
import os
from pathlib import Path
path = Path.home() / ".codex" / "auth.json"
path.write_text(json.dumps({"OPENAI_API_KEY": os.environ["CODEX_KEY"]}, indent=2), encoding="utf-8")
path.chmod(0o600)
PY
    chmod 0600 "$HOME/.codex/config.toml"
  else
    log 'would write ~/.codex/config.toml and ~/.codex/auth.json from CODEX_* environment'
  fi
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
SNAPSHOT_DIR="$STATE_ROOT/install-snapshots"
if [ -n "${AI_BRIDGE_SHARED_SECRET:-}" ]; then
  BRIDGE_SECRET="$AI_BRIDGE_SHARED_SECRET"
else
  BRIDGE_SECRET="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
fi
case "$BRIDGE_SECRET" in
  *[!A-Za-z0-9_-]*|'')
    log 'AI_BRIDGE_SHARED_SECRET must be a non-empty base64url secret'
    exit 1
    ;;
esac
if [ "$DRY_RUN" = false ]; then
  sudo mkdir -p "$SNAPSHOT_DIR"
  SNAPSHOT_CONFIG_ENV_JSON=null
  if [ -f "$STATE_ROOT/config.env" ]; then
    sudo cp "$STATE_ROOT/config.env" "$SNAPSHOT_DIR/config.env.preinstall"
    sudo chmod 0600 "$SNAPSHOT_DIR/config.env.preinstall"
    SNAPSHOT_CONFIG_ENV_JSON="\"$SNAPSHOT_DIR/config.env.preinstall\""
  fi
  sudo tee "$STATE_ROOT/config.env" >/dev/null <<EOF
AI_REMOTE_STATE=$STATE_ROOT
AI_WORKSPACE_ROOT=$WORKSPACE_ROOT
CLAUDE_MODEL=$CLAUDE_MODEL
CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=${CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC:-1}
AI_BRIDGE_SHARED_SECRET=$BRIDGE_SECRET
EOF
  if [ -n "${ANTHROPIC_BASE_URL:-}" ]; then
    printf 'ANTHROPIC_BASE_URL=%s\n' "$ANTHROPIC_BASE_URL" | sudo tee -a "$STATE_ROOT/config.env" >/dev/null
  fi
  if [ -n "${ANTHROPIC_AUTH_TOKEN:-}" ]; then
    printf 'ANTHROPIC_AUTH_TOKEN=%s\n' "$ANTHROPIC_AUTH_TOKEN" | sudo tee -a "$STATE_ROOT/config.env" >/dev/null
  fi
  if [ -n "${OPENAI_API_KEY:-}" ]; then
    printf 'OPENAI_API_KEY=%s\n' "$OPENAI_API_KEY" | sudo tee -a "$STATE_ROOT/config.env" >/dev/null
  fi
  if [ -n "${CODEX_BASE_URL:-}" ]; then
    printf 'CODEX_BASE_URL=%s\n' "$CODEX_BASE_URL" | sudo tee -a "$STATE_ROOT/config.env" >/dev/null
  fi
  sudo chmod 0600 "$STATE_ROOT/config.env"
else
  log "would write $STATE_ROOT/config.env with a generated bridge secret"
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
    sudo systemctl start ai-remote-runner.service
  else
    log 'would install /etc/systemd/system/ai-remote-runner.service'
    log 'would enable/start ai-remote-runner.service after bridge secret is generated'
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
  claude auth status --json >/dev/null || { log 'claude auth/API config is required before core_ready'; exit 1; }
  log 'defer real Claude print-json smoke to scripts/validate-core-ready.sh to avoid install-time provider spend'
elif [ "$DRY_RUN" = true ]; then
  log 'would run claude auth status; real print-json smoke is deferred to core-ready validation'
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
  "core_ready": false,
  "core_ready_status": "pending_pairing",
  "codex_ready": $CODEX_READY,
  "codex_status": "$CODEX_STATUS",
  "codex_remediation_zh": "$CODEX_REMEDIATION_ZH",
  "claude_model": "$CLAUDE_MODEL",
  "snapshots": {
    "config_env": $SNAPSHOT_CONFIG_ENV_JSON
  },
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
if [ "$DRY_RUN" = false ] && [ -n "${MATTERMOST_WEBHOOK_URL:-}" ]; then
  "$SCRIPT_DIR/validate-core-ready.sh"
else
  log 'skip core_ready validation until pairing supplies bridge secret and Mattermost webhook'
fi
