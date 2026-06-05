#!/usr/bin/env bash
set -euo pipefail

DRY_RUN=false
ENABLE_TELEGRAM="${ENABLE_TELEGRAM:-false}"
STATE_ROOT="${AI_REMOTE_STATE:-/var/lib/ai-remote-runner}"
WORKSPACE_ROOT="${AI_WORKSPACE_ROOT:-/srv/ai-workspaces}"
INSTALL_ROOT="${AI_REMOTE_INSTALL_ROOT:-/opt/ai-remote-runner}"
CLAUDE_MODEL="${CLAUDE_MODEL:-claude-opus-4-6-20260130}"
AI_DEFAULT_PROVIDER="${AI_DEFAULT_PROVIDER:-}"
AI_RUNNER_PROVIDERS="${AI_RUNNER_PROVIDERS:-}"
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

apt_install() {
  if [ "$DRY_RUN" = true ]; then
    run sudo apt-get install -y "$@"
  else
    sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y "$@"
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

REQUEST_CLAUDE=false
REQUEST_CODEX=false
if [ -z "$AI_RUNNER_PROVIDERS" ]; then
  if [ -n "$AI_DEFAULT_PROVIDER" ]; then
    AI_RUNNER_PROVIDERS="$AI_DEFAULT_PROVIDER"
  else
    AI_RUNNER_PROVIDERS="claude-code,codex"
  fi
fi
for provider in ${AI_RUNNER_PROVIDERS//,/ }; do
  case "$provider" in
    all|both)
      REQUEST_CLAUDE=true
      REQUEST_CODEX=true
      ;;
    claude|claude-code|codex)
      normalized_provider="$(normalize_provider "$provider")"
      case "$normalized_provider" in
        claude-code) REQUEST_CLAUDE=true ;;
        codex) REQUEST_CODEX=true ;;
      esac
      ;;
    *)
      log "AI_RUNNER_PROVIDERS contains unsupported provider: $provider"
      exit 2
      ;;
  esac
done
if [ "$REQUEST_CLAUDE" = false ] && [ "$REQUEST_CODEX" = false ]; then
  log 'AI_RUNNER_PROVIDERS must request claude-code, codex, or both'
  exit 2
fi
if [ "$REQUEST_CLAUDE" = true ] && [ "$REQUEST_CODEX" = true ]; then
  AI_RUNNER_PROVIDERS="claude-code,codex"
elif [ "$REQUEST_CLAUDE" = true ]; then
  AI_RUNNER_PROVIDERS="claude-code"
else
  AI_RUNNER_PROVIDERS="codex"
fi
if [ -z "$AI_DEFAULT_PROVIDER" ] && { [ "$REQUEST_CLAUDE" = false ] || [ "$REQUEST_CODEX" = false ]; }; then
  AI_DEFAULT_PROVIDER="$AI_RUNNER_PROVIDERS"
fi
if [ -n "$AI_DEFAULT_PROVIDER" ]; then
  AI_DEFAULT_PROVIDER="$(normalize_provider "$AI_DEFAULT_PROVIDER")" || { log 'AI_DEFAULT_PROVIDER must be claude-code, claude, or codex'; exit 2; }
  case ",$AI_RUNNER_PROVIDERS," in
    *",$AI_DEFAULT_PROVIDER,"*) ;;
    *) log "AI_DEFAULT_PROVIDER=$AI_DEFAULT_PROVIDER must be included in AI_RUNNER_PROVIDERS=$AI_RUNNER_PROVIDERS"; exit 2 ;;
  esac
fi

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

log 'stage 03: install or verify requested Claude Code provider'
if [ "$REQUEST_CLAUDE" = true ]; then
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
else
  log 'skip Claude Code install; provider not requested'
fi

log 'stage 04: install or verify requested Codex CLI provider'
CODEX_NPM_PACKAGE="$(read_lock codex_npm_package || true)"
CODEX_NPM_VERSION="$(read_lock codex_npm_version || true)"
CODEX_NPM_PACKAGE="${CODEX_NPM_PACKAGE:-@openai/codex}"
CODEX_NPM_VERSION="${CODEX_NPM_VERSION:-0.137.0}"
CODEX_READY=false
CODEX_STATUS="external_prerequisite"
CODEX_REMEDIATION_ZH="Codex CLI 未安装；请按当前 Codex 官方安装说明手动安装后重新运行 /ai 提供商 列表。"
if [ "$REQUEST_CODEX" = true ]; then
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
else
  CODEX_STATUS="not_requested"
  CODEX_REMEDIATION_ZH=""
  log 'skip Codex CLI install; provider not requested'
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
elif [ -n "$(config_value AI_BRIDGE_SHARED_SECRET)" ]; then
  BRIDGE_SECRET="$(config_value AI_BRIDGE_SHARED_SECRET)"
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
  PREVIOUS_TELEGRAM_ALLOW_ALL_CHATS="$(config_value TELEGRAM_ALLOW_ALL_CHATS)"
  PREVIOUS_TELEGRAM_API_BASE="$(config_value TELEGRAM_API_BASE)"
  PREVIOUS_TELEGRAM_RESERVED_USD="$(config_value TELEGRAM_RESERVED_USD)"
  sudo tee "$STATE_ROOT/config.env" >/dev/null <<EOF
AI_REMOTE_STATE=$STATE_ROOT
AI_WORKSPACE_ROOT=$WORKSPACE_ROOT
AI_RUNNER_PROVIDERS=$AI_RUNNER_PROVIDERS
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
  for key in MATTERMOST_PLATFORM_URL MATTERMOST_WEBHOOK_URL MATTERMOST_BOT_TOKEN MATTERMOST_SLASH_TOKEN AI_BRIDGE_SECRET_TRANSFER_METHOD TELEGRAM_BOT_TOKEN TELEGRAM_ALLOWED_CHAT_IDS TELEGRAM_ALLOW_ALL_CHATS TELEGRAM_API_BASE TELEGRAM_RESERVED_USD; do
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
        TELEGRAM_ALLOW_ALL_CHATS) value="$PREVIOUS_TELEGRAM_ALLOW_ALL_CHATS" ;;
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
    if [ "$ENABLE_TELEGRAM" = true ]; then
      sudo tee /etc/systemd/system/ai-telegram-bot.service >/dev/null <<EOF
[Unit]
Description=AI Remote Runner Telegram Bot
After=network-online.target ai-remote-runner.service
Wants=network-online.target

[Service]
Type=simple
EnvironmentFile=$STATE_ROOT/config.env
WorkingDirectory=$INSTALL_ROOT
ExecStart=/usr/bin/python3 -m ai_remote_runner.cli telegram
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
      sudo systemctl daemon-reload
      if [ -n "$(config_value TELEGRAM_BOT_TOKEN)" ] || [ -n "${TELEGRAM_BOT_TOKEN:-}" ]; then
        sudo systemctl enable --now ai-telegram-bot.service
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
    sudo tee "$INSTALL_ROOT/run-local.sh" >/dev/null <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
source "${AI_REMOTE_STATE:-/var/lib/ai-remote-runner}/config.env"
exec python3 -m ai_remote_runner.cli bridge --host 127.0.0.1 --port 8765
EOF
    sudo chmod +x "$INSTALL_ROOT/run-local.sh"
    if [ "$ENABLE_TELEGRAM" = true ]; then
      sudo tee "$INSTALL_ROOT/run-telegram-local.sh" >/dev/null <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
source "${AI_REMOTE_STATE:-/var/lib/ai-remote-runner}/config.env"
exec python3 -m ai_remote_runner.cli telegram
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

log 'stage 09: connect runner to communication platform'
log 'use scripts/pair-runner.sh with Mattermost URL, webhook URL, bot token, and bridge shared secret'
if [ "$ENABLE_TELEGRAM" = true ]; then
  log 'use scripts/pair-telegram.sh with a BotFather token and Telegram chat_id to enable Telegram'
fi

log 'stage 10: run provider smoke tests'
python3 -m ai_remote_runner.cli providers
if [ "$DRY_RUN" = false ] && [ "$REQUEST_CLAUDE" = true ]; then
  command -v claude >/dev/null 2>&1 || { log 'claude is required before core_ready for requested provider claude-code'; exit 1; }
  claude auth status --json >/dev/null || { log 'claude auth/API config is required before core_ready for requested provider claude-code'; exit 1; }
  log 'defer real Claude print-json smoke to scripts/validate-core-ready.sh to avoid install-time provider spend'
elif [ "$DRY_RUN" = false ] && [ "$REQUEST_CODEX" = true ]; then
  command -v codex >/dev/null 2>&1 || { log 'codex is required before core_ready for AI_DEFAULT_PROVIDER=codex'; exit 1; }
  log 'claude auth not required unless claude-code provider is requested'
elif [ "$DRY_RUN" = true ]; then
  log "would run provider smoke tests for AI_RUNNER_PROVIDERS=$AI_RUNNER_PROVIDERS"
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
