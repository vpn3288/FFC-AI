#!/usr/bin/env bash
set -euo pipefail

DRY_RUN=false
ENABLE_TELEGRAM="${ENABLE_TELEGRAM:-false}"
STATE_ROOT="${AI_REMOTE_STATE:-/var/lib/ai-remote-runner}"
WORKSPACE_ROOT="${AI_WORKSPACE_ROOT:-/srv/ai-workspaces}"
INSTALL_ROOT="${AI_REMOTE_INSTALL_ROOT:-/opt/ai-remote-runner}"
AI_TOOL_HOME="${AI_TOOL_HOME:-/root}"
CODEX_HOME="${AI_CODEX_HOME:-$AI_TOOL_HOME/.codex}"
VSCODE_ROOT_WRAPPER="${AI_VSCODE_ROOT_WRAPPER:-/usr/local/bin/code-root}"
VSCODE_ROOT_DIR="${AI_VSCODE_ROOT_DIR:-/root/.vscode-root}"
SERVICE_PATH="${AI_SERVICE_PATH:-/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin}"
RUNNER_PYTHON="$INSTALL_ROOT/.venv/bin/python"
CLAUDE_MODEL="${CLAUDE_MODEL:-}"
AI_DEFAULT_PROVIDER="${AI_DEFAULT_PROVIDER:-}"
AI_RUNNER_PROVIDERS="${AI_RUNNER_PROVIDERS:-}"
AI_PERMISSION_SCOPE="${AI_PERMISSION_SCOPE:-full}"
AI_REQUIRE_SHELL_CONFIRMATION="${AI_REQUIRE_SHELL_CONFIRMATION:-0}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
export PYTHONPATH="${PYTHONPATH:-$REPO_ROOT/src}"

read_lock() {
  grep "^$1=" "$REPO_ROOT/versions.lock" | cut -d= -f2-
}

usage() {
  printf 'usage: %s [--dry-run] [--enable-telegram]\n' "$0"
}

log() {
  printf '[install-runner] %s\n' "$*"
}

normalize_provider() {
  case "$1" in
    claude) printf 'claude-code' ;;
    claude-code|codex) printf '%s' "$1" ;;
    *) return 1 ;;
  esac
}

run() {
  if [ "$DRY_RUN" = true ]; then
    printf '[dry-run] %s\n' "$*"
  else
    "$@"
  fi
}

root_env_run() {
  if [ "$DRY_RUN" = true ]; then
    printf '[dry-run] root env HOME=%s CODEX_HOME=%s PATH=%s %s\n' "$AI_TOOL_HOME" "$CODEX_HOME" "$SERVICE_PATH" "$*"
  else
    sudo env HOME="$AI_TOOL_HOME" CODEX_HOME="$CODEX_HOME" PATH="$SERVICE_PATH" "$@"
  fi
}

runner_cli() {
  if [ "$DRY_RUN" = true ]; then
    root_env_run "$RUNNER_PYTHON" -m ai_remote_runner.cli "$@"
  else
    root_env_run "$RUNNER_PYTHON" -m ai_remote_runner.cli "$@"
  fi
}

root_has_command() {
  if [ "$DRY_RUN" = true ]; then
    printf '[dry-run] root env PATH=%s command -v %s\n' "$SERVICE_PATH" "$1"
    return 0
  else
    sudo env HOME="$AI_TOOL_HOME" CODEX_HOME="$CODEX_HOME" PATH="$SERVICE_PATH" sh -c "command -v '$1' >/dev/null 2>&1"
  fi
}

apt_install() {
  if [ "$DRY_RUN" = true ]; then
    run sudo apt-get install -y "$@"
  else
    sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y "$@"
  fi
}

install_vscode() {
  log 'stage 05: install or verify VSCode for root/full-access operation'
  VSCODE_READY=false
  if root_has_command code; then
    root_env_run code --version | head -n 1 || true
  elif command -v apt-get >/dev/null 2>&1; then
    log 'code missing; installing latest Visual Studio Code from Microsoft apt repository'
    run sudo install -d -m 0755 /etc/apt/keyrings
    if [ "$DRY_RUN" = false ]; then
      curl -fsSL https://packages.microsoft.com/keys/microsoft.asc -o /tmp/microsoft.asc
      command -v gpg >/dev/null 2>&1 || { log 'gpg required for Microsoft apt key installation'; exit 1; }
      gpg --dearmor /tmp/microsoft.asc | sudo tee /etc/apt/keyrings/packages.microsoft.gpg >/dev/null
      sudo chmod 0644 /etc/apt/keyrings/packages.microsoft.gpg
      echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/packages.microsoft.gpg] https://packages.microsoft.com/repos/code stable main" \
        | sudo tee /etc/apt/sources.list.d/vscode.list >/dev/null
    else
      log 'would install Microsoft apt key and /etc/apt/sources.list.d/vscode.list'
    fi
    run sudo apt-get update
    apt_install code
    if [ "$DRY_RUN" = false ]; then
      root_has_command code || { log 'VSCode install failed; code command is required in the root service PATH'; exit 1; }
      root_env_run code --version | head -n 1 || true
    else
      log 'would verify code --version after package install'
    fi
  else
    log 'code missing and native package installer unavailable; VSCode is required for full-access workstation setup'
    exit 1
  fi
  if [ "$DRY_RUN" = false ]; then
    sudo tee "$VSCODE_ROOT_WRAPPER" >/dev/null <<EOF
#!/usr/bin/env bash
set -euo pipefail
if [ "\$(id -u)" != 0 ]; then
  exec sudo -E "\$0" "\$@"
fi
exec code --no-sandbox --user-data-dir="$VSCODE_ROOT_DIR" --extensions-dir="$VSCODE_ROOT_DIR/extensions" --disable-workspace-trust "\$@"
EOF
    sudo chmod 0755 "$VSCODE_ROOT_WRAPPER"
    sudo mkdir -p "$VSCODE_ROOT_DIR/extensions"
    sudo chown -R root:root "$VSCODE_ROOT_DIR" 2>/dev/null || true
    root_env_run "$VSCODE_ROOT_WRAPPER" --version >/dev/null || { log 'code-root smoke test failed'; exit 1; }
    VSCODE_READY=true
  else
    log "would install $VSCODE_ROOT_WRAPPER root wrapper with --no-sandbox and root user-data/extensions directories"
    VSCODE_READY=true
  fi
}

node_major() {
  command -v node >/dev/null 2>&1 || return 1
  node -v 2>/dev/null | sed -E 's/^v?([0-9]+).*/\1/'
}

ensure_codex_node() {
  local major
  major="$(node_major || true)"
  case "$major" in
    ''|*[!0-9]*) major=0 ;;
  esac
  if [ "$major" -ge 20 ]; then
    return 0
  fi
  if ! command -v apt-get >/dev/null 2>&1; then
    log 'Node.js 20+ is required for Codex CLI; install Node.js 20+ and npm before rerunning'
    return 1
  fi
  log 'Node.js 20+ is required for Codex CLI; installing Node.js 20 from NodeSource'
  if [ "$DRY_RUN" = false ]; then
    curl -fsSL https://deb.nodesource.com/setup_20.x -o /tmp/nodesource_setup_20.x.sh
    sudo env DEBIAN_FRONTEND=noninteractive bash /tmp/nodesource_setup_20.x.sh
  else
    log 'would download and run https://deb.nodesource.com/setup_20.x'
  fi
  apt_install nodejs
  major="$(node_major || true)"
  case "$major" in
    ''|*[!0-9]*) major=0 ;;
  esac
  if [ "$DRY_RUN" = false ] && [ "$major" -lt 20 ]; then
    log 'Node.js 20+ install failed; Codex CLI cannot be installed safely'
    exit 1
  fi
}

config_value() {
  local key="$1"
  if [ "$DRY_RUN" = false ] && [ -f "$STATE_ROOT/config.env" ]; then
    sudo awk -F= -v key="$key" '$1 == key {print substr($0, index($0, "=") + 1); exit}' "$STATE_ROOT/config.env"
  fi
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=true ;;
    --enable-telegram) ENABLE_TELEGRAM=true ;;
    -h|--help) usage; exit 0 ;;
    *) usage; exit 2 ;;
  esac
  shift
done

REQUEST_CLAUDE=true
REQUEST_CODEX=true
if [ -n "$AI_RUNNER_PROVIDERS" ] && [ "$AI_RUNNER_PROVIDERS" != "claude-code,codex" ]; then
  log "AI_RUNNER_PROVIDERS=$AI_RUNNER_PROVIDERS ignored; core install requires claude-code,codex"
fi
AI_RUNNER_PROVIDERS="claude-code,codex"
if [ -n "$AI_DEFAULT_PROVIDER" ]; then
  AI_DEFAULT_PROVIDER="$(normalize_provider "$AI_DEFAULT_PROVIDER")" || { log 'AI_DEFAULT_PROVIDER must be claude-code, claude, or codex'; exit 2; }
  case ",$AI_RUNNER_PROVIDERS," in
    *",$AI_DEFAULT_PROVIDER,"*) ;;
    *) log "AI_DEFAULT_PROVIDER=$AI_DEFAULT_PROVIDER must be included in AI_RUNNER_PROVIDERS=$AI_RUNNER_PROVIDERS"; exit 2 ;;
  esac
fi
case "$AI_PERMISSION_SCOPE" in
  chat|edit|shell|full) ;;
  *) log 'AI_PERMISSION_SCOPE must be chat, edit, shell, or full'; exit 2 ;;
esac
case "$AI_REQUIRE_SHELL_CONFIRMATION" in
  0|1|true|false|yes|no) ;;
  *) log 'AI_REQUIRE_SHELL_CONFIRMATION must be 0/1, true/false, or yes/no'; exit 2 ;;
esac

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
  apt_install python3 python3-pip python3-venv ca-certificates curl git openssl gpg
else
  log 'apt-get unavailable; package installation skipped and must be handled externally'
fi

log 'stage 03: install or verify requested Claude Code provider'
if [ "$REQUEST_CLAUDE" = true ]; then
  if root_has_command claude; then
    root_env_run claude --version
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
    root_has_command claude || { log 'claude install failed or is not available in the root service PATH'; exit 1; }
    root_env_run claude --version
  else
    log 'claude missing and native installer unavailable; install from official Claude Code docs before core_ready'
    exit 1
  fi
fi

log 'stage 04: install or verify requested Codex CLI provider'
CODEX_NPM_PACKAGE="$(read_lock codex_npm_package || true)"
CODEX_NPM_VERSION="$(read_lock codex_npm_version || true)"
CODEX_NPM_PACKAGE="${CODEX_NPM_PACKAGE:-@openai/codex}"
CODEX_NPM_VERSION="${CODEX_NPM_VERSION:-0.137.0}"
CODEX_READY=false
CODEX_STATUS="install_required"
CODEX_REMEDIATION_ZH="Codex CLI 必须由安装脚本全局安装；若失败请检查网络、Node.js/npm、以及版本锁。"
if [ "$REQUEST_CODEX" = true ]; then
  if root_has_command codex; then
    root_env_run codex --version
    CODEX_READY=true
    CODEX_STATUS="installed"
    CODEX_REMEDIATION_ZH=""
  elif command -v apt-get >/dev/null 2>&1; then
    log "codex missing; installing $CODEX_NPM_PACKAGE@$CODEX_NPM_VERSION through npm"
    ensure_codex_node
    if [ "$DRY_RUN" = false ]; then
      command -v npm >/dev/null 2>&1 || { log 'npm missing after Node.js install; Codex CLI cannot be installed'; exit 1; }
      sudo npm install -g "$CODEX_NPM_PACKAGE@$CODEX_NPM_VERSION"
      root_has_command codex || { log 'codex npm install did not place codex on the root service PATH'; exit 1; }
      root_env_run codex --version
      CODEX_READY=true
      CODEX_STATUS="installed"
      CODEX_REMEDIATION_ZH=""
    else
      log "would run sudo npm install -g $CODEX_NPM_PACKAGE@$CODEX_NPM_VERSION"
    fi
  else
    log 'codex missing and native package installer unavailable; Codex CLI is required for core install'
    exit 1
  fi
else
  log 'Codex CLI is required for core install'
  exit 1
fi
if [ -n "${CODEX_API_KEY:-}" ] || [ -n "${OPENAI_API_KEY:-}" ]; then
  CODEX_KEY="${CODEX_API_KEY:-$OPENAI_API_KEY}"
  if [ "$DRY_RUN" = false ]; then
    sudo mkdir -p "$CODEX_HOME"
    sudo tee "$CODEX_HOME/config.toml" >/dev/null <<EOF
model_provider = "${CODEX_MODEL_PROVIDER:-OpenAI}"
model = "${CODEX_MODEL:-gpt-5.5}"
review_model = "${CODEX_REVIEW_MODEL:-${CODEX_MODEL:-gpt-5.5}}"
model_reasoning_effort = "${CODEX_REASONING_EFFORT:-xhigh}"
disable_response_storage = ${CODEX_DISABLE_RESPONSE_STORAGE:-false}
network_access = "enabled"
approval_policy = "never"
sandbox_mode = "danger-full-access"
workspace_write_network_access = true
dangerously_bypass_approvals_and_sandbox = true
model_context_window = ${CODEX_CONTEXT_WINDOW:-200000}
model_auto_compact_token_limit = ${CODEX_AUTO_COMPACT_TOKEN_LIMIT:-160000}
hide_agent_reasoning = false

[shell_environment_policy]
inherit = "all"

[model_providers.${CODEX_MODEL_PROVIDER:-OpenAI}]
name = "${CODEX_MODEL_PROVIDER:-OpenAI}"
base_url = "${CODEX_BASE_URL:-https://api.openai.com/v1}"
wire_api = "responses"
requires_openai_auth = true

[features]
goals = true
EOF
    sudo env CODEX_KEY="$CODEX_KEY" CODEX_HOME="$CODEX_HOME" python3 - <<'PY'
import json
import os
from pathlib import Path
path = Path(os.environ["CODEX_HOME"]) / "auth.json"
path.write_text(json.dumps({"OPENAI_API_KEY": os.environ["CODEX_KEY"]}, indent=2), encoding="utf-8")
path.chmod(0o600)
PY
    sudo chmod 0600 "$CODEX_HOME/config.toml" "$CODEX_HOME/auth.json"
    sudo chown -R root:root "$CODEX_HOME" 2>/dev/null || true
  else
    log "would write $CODEX_HOME/config.toml and $CODEX_HOME/auth.json from CODEX_* environment"
  fi
fi

install_vscode

log 'stage 06: create runner directories'
run sudo mkdir -p "$STATE_ROOT"/{credentials,instructions/snapshots,budget} "$WORKSPACE_ROOT" "$INSTALL_ROOT"
run sudo rm -rf "$INSTALL_ROOT/src"
run sudo cp -R "$REPO_ROOT"/src "$INSTALL_ROOT"/
run sudo cp "$REPO_ROOT"/pyproject.toml "$INSTALL_ROOT"/
if [ "$DRY_RUN" = false ]; then
  sudo python3 -m venv "$INSTALL_ROOT/.venv"
  (cd "$INSTALL_ROOT" && sudo "$RUNNER_PYTHON" -m pip install -e .)
else
  log "would create $INSTALL_ROOT/.venv and install Python package from $INSTALL_ROOT"
fi

log 'stage 07: create runner configuration files'
SNAPSHOT_DIR="$STATE_ROOT/install-snapshots"
if [ -n "${AI_BRIDGE_SHARED_SECRET:-}" ]; then
  BRIDGE_SECRET="$AI_BRIDGE_SHARED_SECRET"
elif [ -n "$(config_value AI_BRIDGE_SHARED_SECRET)" ]; then
  BRIDGE_SECRET="$(config_value AI_BRIDGE_SHARED_SECRET)"
elif [ "$DRY_RUN" = true ]; then
  BRIDGE_SECRET="DRY_RUN_BRIDGE_SECRET_PLACEHOLDER_000000000000"
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
  PREVIOUS_MATTERMOST_PLATFORM_URL="$(config_value MATTERMOST_PLATFORM_URL)"
  PREVIOUS_MATTERMOST_WEBHOOK_URL="$(config_value MATTERMOST_WEBHOOK_URL)"
  PREVIOUS_MATTERMOST_BOT_TOKEN="$(config_value MATTERMOST_BOT_TOKEN)"
  PREVIOUS_MATTERMOST_SLASH_TOKEN="$(config_value MATTERMOST_SLASH_TOKEN)"
  PREVIOUS_AI_BRIDGE_SECRET_TRANSFER_METHOD="$(config_value AI_BRIDGE_SECRET_TRANSFER_METHOD)"
  PREVIOUS_TELEGRAM_BOT_TOKEN="$(config_value TELEGRAM_BOT_TOKEN)"
  PREVIOUS_TELEGRAM_ALLOWED_CHAT_IDS="$(config_value TELEGRAM_ALLOWED_CHAT_IDS)"
  PREVIOUS_TELEGRAM_API_BASE="$(config_value TELEGRAM_API_BASE)"
  PREVIOUS_TELEGRAM_RESERVED_USD="$(config_value TELEGRAM_RESERVED_USD)"
  sudo tee "$STATE_ROOT/config.env" >/dev/null <<EOF
AI_REMOTE_STATE=$STATE_ROOT
AI_WORKSPACE_ROOT=$WORKSPACE_ROOT
AI_RUNNER_PROVIDERS=$AI_RUNNER_PROVIDERS
AI_PERMISSION_SCOPE=$AI_PERMISSION_SCOPE
AI_REQUIRE_SHELL_CONFIRMATION=$AI_REQUIRE_SHELL_CONFIRMATION
HOME=$AI_TOOL_HOME
CODEX_HOME=$CODEX_HOME
PATH=$SERVICE_PATH
CLAUDE_MODEL=$CLAUDE_MODEL
CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=${CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC:-0}
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
  for key in MATTERMOST_PLATFORM_URL MATTERMOST_WEBHOOK_URL MATTERMOST_BOT_TOKEN MATTERMOST_SLASH_TOKEN AI_BRIDGE_SECRET_TRANSFER_METHOD TELEGRAM_BOT_TOKEN TELEGRAM_ALLOWED_CHAT_IDS TELEGRAM_API_BASE TELEGRAM_RESERVED_USD; do
    value="$(printenv "$key" || true)"
    if [ -z "$value" ]; then
      case "$key" in
        MATTERMOST_PLATFORM_URL) value="$PREVIOUS_MATTERMOST_PLATFORM_URL" ;;
        MATTERMOST_WEBHOOK_URL) value="$PREVIOUS_MATTERMOST_WEBHOOK_URL" ;;
        MATTERMOST_BOT_TOKEN) value="$PREVIOUS_MATTERMOST_BOT_TOKEN" ;;
        MATTERMOST_SLASH_TOKEN) value="$PREVIOUS_MATTERMOST_SLASH_TOKEN" ;;
        AI_BRIDGE_SECRET_TRANSFER_METHOD) value="$PREVIOUS_AI_BRIDGE_SECRET_TRANSFER_METHOD" ;;
        TELEGRAM_BOT_TOKEN) value="$PREVIOUS_TELEGRAM_BOT_TOKEN" ;;
        TELEGRAM_ALLOWED_CHAT_IDS) value="$PREVIOUS_TELEGRAM_ALLOWED_CHAT_IDS" ;;
        TELEGRAM_API_BASE) value="$PREVIOUS_TELEGRAM_API_BASE" ;;
        TELEGRAM_RESERVED_USD) value="$PREVIOUS_TELEGRAM_RESERVED_USD" ;;
      esac
    fi
    if [ -n "$value" ]; then
      printf '%s=%s\n' "$key" "$value" | sudo tee -a "$STATE_ROOT/config.env" >/dev/null
    fi
  done
  sudo chmod 0600 "$STATE_ROOT/config.env"
  if [ -n "$AI_DEFAULT_PROVIDER" ]; then
    sudo mkdir -p "$STATE_ROOT"
    python3 - "$AI_DEFAULT_PROVIDER" <<'PY' | sudo tee "$STATE_ROOT/provider-selection.json" >/dev/null
import json
import sys
provider = sys.argv[1]
if provider not in {"claude-code", "codex"}:
    raise SystemExit("AI_DEFAULT_PROVIDER must be claude-code or codex")
print(json.dumps({"provider": provider}, indent=2, sort_keys=True))
PY
    sudo chmod 0600 "$STATE_ROOT/provider-selection.json"
  fi
  sudo env AI_PERMISSION_SCOPE="$AI_PERMISSION_SCOPE" python3 - "$STATE_ROOT/conversation-policy.json" <<'PY'
import json
import os
import sys
from pathlib import Path

path = Path(sys.argv[1])
scope = os.environ["AI_PERMISSION_SCOPE"]
default = {
    "policy": "continue",
    "conversation_id": "default",
    "provider_conversations": {},
    "auto_compact_enabled": True,
    "auto_compact_threshold_percent": 80,
}
try:
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
except json.JSONDecodeError:
    data = {}
data = default | data
data["permission_scope"] = scope
path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
path.chmod(0o600)
PY
else
  log "would write $STATE_ROOT/config.env with a generated bridge secret"
  log "would set conversation policy permission_scope=$AI_PERMISSION_SCOPE"
fi

log 'stage 08: create credential broker storage backend'
run sudo chmod 0700 "$STATE_ROOT/credentials"

log 'stage 09: install runner bridge service'
if [ "$SYSTEMD" = true ]; then
  if [ "$DRY_RUN" = false ]; then
    sudo tee /etc/systemd/system/ai-remote-runner.service >/dev/null <<EOF
[Unit]
Description=AI Remote Runner Bridge
After=network-online.target

[Service]
Type=simple
User=root
EnvironmentFile=$STATE_ROOT/config.env
WorkingDirectory=$INSTALL_ROOT
ExecStart=$RUNNER_PYTHON -m ai_remote_runner.cli bridge --host 127.0.0.1 --port 8765
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
    sudo systemctl daemon-reload
    sudo systemctl enable ai-remote-runner.service
    sudo systemctl restart ai-remote-runner.service
    if [ "$ENABLE_TELEGRAM" = true ]; then
      sudo tee /etc/systemd/system/ai-telegram-bot.service >/dev/null <<EOF
[Unit]
Description=AI Remote Runner Telegram Bot
After=network-online.target ai-remote-runner.service
Wants=network-online.target

[Service]
Type=simple
User=root
EnvironmentFile=$STATE_ROOT/config.env
WorkingDirectory=$INSTALL_ROOT
ExecStart=$RUNNER_PYTHON -m ai_remote_runner.cli telegram
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
      sudo systemctl daemon-reload
      if [ -n "$(config_value TELEGRAM_BOT_TOKEN)" ] || [ -n "${TELEGRAM_BOT_TOKEN:-}" ]; then
        sudo systemctl enable --now ai-telegram-bot.service
        sudo systemctl restart ai-telegram-bot.service
      else
        log 'Telegram service installed but not started; run scripts/pair-telegram.sh after creating a BotFather token'
      fi
    fi
  else
    log 'would install /etc/systemd/system/ai-remote-runner.service'
    log 'would enable/start ai-remote-runner.service after bridge secret is generated'
    if [ "$ENABLE_TELEGRAM" = true ]; then
      log 'would install /etc/systemd/system/ai-telegram-bot.service; it starts after Telegram pairing supplies a bot token'
    fi
  fi
else
  if [ "$DRY_RUN" = false ]; then
    sudo tee "$INSTALL_ROOT/run-local.sh" >/dev/null <<EOF
#!/usr/bin/env bash
set -euo pipefail
if [ "\$(id -u)" != 0 ]; then
  exec sudo -E "\$0" "\$@"
fi
source "$STATE_ROOT/config.env"
exec "$RUNNER_PYTHON" -m ai_remote_runner.cli bridge --host 127.0.0.1 --port 8765
EOF
    sudo chmod +x "$INSTALL_ROOT/run-local.sh"
    if [ "$ENABLE_TELEGRAM" = true ]; then
      sudo tee "$INSTALL_ROOT/run-telegram-local.sh" >/dev/null <<EOF
#!/usr/bin/env bash
set -euo pipefail
if [ "\$(id -u)" != 0 ]; then
  exec sudo -E "\$0" "\$@"
fi
source "$STATE_ROOT/config.env"
exec "$RUNNER_PYTHON" -m ai_remote_runner.cli telegram
EOF
      sudo chmod +x "$INSTALL_ROOT/run-telegram-local.sh"
    fi
  else
    log "would write $INSTALL_ROOT/run-local.sh"
    if [ "$ENABLE_TELEGRAM" = true ]; then
      log "would write $INSTALL_ROOT/run-telegram-local.sh"
    fi
  fi
fi

log 'stage 10: connect runner to communication platform'
log 'use scripts/pair-runner.sh with Mattermost URL, webhook URL, bot token, and bridge shared secret'
if [ "$ENABLE_TELEGRAM" = true ]; then
  log 'use scripts/pair-telegram.sh with a BotFather token and Telegram chat_id to enable Telegram'
fi

log 'stage 11: run provider CLI/auth preflight; full-access smoke is enforced by validate-core-ready.sh'
runner_cli providers
if [ "$DRY_RUN" = false ] && [ "$REQUEST_CLAUDE" = true ]; then
  root_has_command claude || { log 'claude is required before core_ready for requested provider claude-code'; exit 1; }
  root_env_run claude auth status --json >/dev/null || { log 'claude auth/API config is required before core_ready for requested provider claude-code'; exit 1; }
fi
if [ "$DRY_RUN" = false ] && [ "$REQUEST_CODEX" = true ]; then
  root_has_command codex || { log 'codex is required before core_ready for requested provider codex'; exit 1; }
  root_env_run codex exec --help >/dev/null || { log 'codex exec is required before core_ready for requested provider codex'; exit 1; }
  if root_env_run codex exec --help 2>&1 | grep -Eq -- '--dangerously-bypass-approvals-and-sandbox|--sandbox'; then
    log 'codex full-access exec flag is available'
  else
    log 'codex full-access exec flag is unavailable'
    exit 1
  fi
fi
if [ "$DRY_RUN" = true ]; then
  log "would run provider CLI/auth preflight for AI_RUNNER_PROVIDERS=$AI_RUNNER_PROVIDERS"
fi

log 'stage 12: run phone command smoke tests'
runner_cli parse '/ai 状态'
runner_cli index >/dev/null

log 'stage 13: report core_ready or failed'
if [ "$DRY_RUN" = false ]; then
  sudo tee "$STATE_ROOT/install-manifest.json" >/dev/null <<EOF
{
  "component": "ai-remote-runner",
  "state_root": "$STATE_ROOT",
  "workspace_root": "$WORKSPACE_ROOT",
  "install_root": "$INSTALL_ROOT",
  "ai_tool_home": "$AI_TOOL_HOME",
  "codex_home": "$CODEX_HOME",
  "systemd": $SYSTEMD,
  "wsl": $WSL,
  "core_ready": false,
  "core_ready_status": "pending_pairing",
  "codex_ready": $CODEX_READY,
  "codex_status": "$CODEX_STATUS",
  "codex_remediation_zh": "$CODEX_REMEDIATION_ZH",
  "vscode_ready": $VSCODE_READY,
  "vscode_root_wrapper": "$VSCODE_ROOT_WRAPPER",
  "vscode_root_dir": "$VSCODE_ROOT_DIR",
  "claude_model": "$CLAUDE_MODEL",
  "permission_scope": "$AI_PERMISSION_SCOPE",
  "shell_confirmation_required": "$AI_REQUIRE_SHELL_CONFIRMATION",
  "telegram_enabled": $ENABLE_TELEGRAM,
  "telegram_status": "pending_pairing",
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
  bash "$SCRIPT_DIR/validate-core-ready.sh"
else
  log 'skip core_ready validation until pairing supplies bridge secret and Mattermost webhook'
fi
