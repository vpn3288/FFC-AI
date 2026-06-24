#!/usr/bin/env bash
set -euo pipefail

DRY_RUN=false
ENABLE_TELEGRAM="${ENABLE_TELEGRAM:-false}"
AI_RUNNER_COMPONENTS="${AI_RUNNER_COMPONENTS:-}"
AI_WRITE_CLAUDE_SETTINGS="${AI_WRITE_CLAUDE_SETTINGS:-auto}"
STATE_ROOT="${AI_REMOTE_STATE:-/var/lib/ai-remote-runner}"
WORKSPACE_ROOT="${AI_WORKSPACE_ROOT:-/srv/ai-workspaces}"
INSTALL_ROOT="${AI_REMOTE_INSTALL_ROOT:-/opt/ai-remote-runner}"
AI_TOOL_HOME="${AI_TOOL_HOME:-/root}"
CODEX_HOME="${AI_CODEX_HOME:-$AI_TOOL_HOME/.codex}"
VSCODE_ROOT_WRAPPER="${AI_VSCODE_ROOT_WRAPPER:-/usr/local/bin/code-root}"
VSCODE_ROOT_DIR="${AI_VSCODE_ROOT_DIR:-/root/.vscode-root}"
VSCODE_SETTINGS_DIR="${AI_VSCODE_SETTINGS_DIR:-$VSCODE_ROOT_DIR/User}"
SERVICE_PATH="${AI_SERVICE_PATH:-/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin}"
AI_SERVICE_TERM="${AI_SERVICE_TERM:-xterm-256color}"
RUNNER_PYTHON="$INSTALL_ROOT/.venv/bin/python"
CLAUDE_MODEL="${CLAUDE_MODEL:-}"
CLAUDE_API_RETRY_ATTEMPTS="${CLAUDE_API_RETRY_ATTEMPTS:-}"
CLAUDE_API_RETRY_SLEEP_SECONDS="${CLAUDE_API_RETRY_SLEEP_SECONDS:-}"
CODEX_MODEL="${CODEX_MODEL:-}"
CODEX_REVIEW_MODEL="${CODEX_REVIEW_MODEL:-}"
CODEX_OPENAI_COMPAT_PROVIDER="${CODEX_OPENAI_COMPAT_PROVIDER:-ffc_openai_compat}"

# 确定VSCode Claude模型（优先级：VSCODE_CLAUDE_MODEL > VSCODE_MODEL > 默认值）
if [ -n "${VSCODE_CLAUDE_MODEL:-}" ]; then
  VSCODE_CLAUDE_MODEL="$VSCODE_CLAUDE_MODEL"
elif [ -n "${VSCODE_MODEL:-}" ]; then
  VSCODE_CLAUDE_MODEL="$VSCODE_MODEL"
else
  VSCODE_CLAUDE_MODEL="gpt-5.5"
fi
VSCODE_CLAUDE_MAX_TURNS="${VSCODE_CLAUDE_MAX_TURNS:-}"
VSCODE_CLAUDE_API_RETRY_ATTEMPTS="${VSCODE_CLAUDE_API_RETRY_ATTEMPTS:-}"
VSCODE_CLAUDE_API_RETRY_SLEEP_SECONDS="${VSCODE_CLAUDE_API_RETRY_SLEEP_SECONDS:-}"
AI_TASK_RESERVED_USD="${AI_TASK_RESERVED_USD:-}"
AI_DEFAULT_PROVIDER="${AI_DEFAULT_PROVIDER:-}"
AI_RUNNER_PROVIDERS="${AI_RUNNER_PROVIDERS:-}"
AI_PERMISSION_SCOPE="${AI_PERMISSION_SCOPE:-full}"
AI_TASK_TIMEOUT_SECONDS="${AI_TASK_TIMEOUT_SECONDS:-}"
AI_REQUIRE_SHELL_CONFIRMATION="${AI_REQUIRE_SHELL_CONFIRMATION:-0}"
AI_PROCESS_CONTROL_ENABLED="${AI_PROCESS_CONTROL_ENABLED:-1}"
AI_INSTALL_CC_SWITCH="${AI_INSTALL_CC_SWITCH:-auto}"
AI_PRIMARY_PROVIDERS_CSV="claude-code,vscode,codex"
AI_PRIMARY_PROVIDER_USAGE="all,telegram | codex,telegram | claude-code,telegram | vscode,telegram | vscode | cc-switch"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
export PYTHONPATH="${PYTHONPATH:-$REPO_ROOT/src}"

read_lock() {
  grep "^$1=" "$REPO_ROOT/versions.lock" | cut -d= -f2- | tr -d '\r'
}

usage() {
  printf 'usage: %s [--dry-run] [--enable-telegram]\n' "$0"
  printf '       AI_RUNNER_COMPONENTS is required: %s\n' "$AI_PRIMARY_PROVIDER_USAGE"
  printf '       all/full/core install Claude Code, Codex, VSCode, and the runner on one VM.\n'
  printf '       Set AI_INSTALL_CC_SWITCH=true or add cc-switch to install the optional Debian CC Switch desktop app.\n'
}

log() {
  printf '[install-runner] %s\n' "$*"
}

normalize_provider() {
  case "$1" in
    claude) printf 'claude-code' ;;
    code|vs-code) printf 'vscode' ;;
    claude-code|vscode|codex) printf '%s' "$1" ;;
    *) return 1 ;;
  esac
}

strip_model_target_prefix() {
  local raw="$1"
  local -a parts
  read -r -a parts <<<"$raw"
  local first="${parts[0]:-}"
  local second="${parts[1]:-}"
  local third="${parts[2]:-}"
  if [ "${#parts[@]}" -gt 1 ]; then
    case "${first,,} ${second,,} ${third,,}" in
      "visual studio code") parts=("${parts[@]:3}") ;;
    esac
  fi
  if [ "${#parts[@]}" -gt 1 ]; then
    first="${parts[0]:-}"
    second="${parts[1]:-}"
    case "${first,,} ${second,,}" in
      "claude code"|"vs code") parts=("${parts[@]:2}") ;;
    esac
  fi
  if [ "${#parts[@]}" -gt 1 ]; then
    first="${parts[0]:-}"
    case "${first,,}" in
      anthropic|claude|claude-code|claudecode|code|codex|openai|vs-code|vscode) parts=("${parts[@]:1}") ;;
    esac
  fi
  raw="${parts[*]}"
  if [[ "$raw" == *" "* ]]; then
    printf ''
    return 0
  fi
  printf '%s' "$raw"
}

normalize_gpt_model_alias() {
  local raw
  raw="$(strip_model_target_prefix "$1")"
  if [ -z "$raw" ]; then
    printf ''
    return 0
  fi
  local normalized
  normalized="$(printf '%s' "$raw" | tr '[:upper:]' '[:lower:]')"
  case "$normalized" in
    gpt|gpt5|gpt-5|gpt5.5|openai) printf 'gpt-5.5' ;;
    codex) printf 'gpt-5.5' ;;
    *) printf '%s' "$raw" ;;
  esac
}

normalize_claude_model_alias() {
  local raw
  raw="$(strip_model_target_prefix "$1")"
  if [ -z "$raw" ]; then
    printf ''
    return 0
  fi
  local normalized
  normalized="$(printf '%s' "$raw" | tr '[:upper:]' '[:lower:]')"
  case "$normalized" in
    anthropic|claude|claude-opus) printf 'opus' ;;
    claude-sonnet) printf 'sonnet' ;;
    *) printf '%s' "$raw" ;;
  esac
}

normalize_model_alias() {
  local raw normalized
  raw="$(strip_model_target_prefix "$1")"
  normalized="$(printf '%s' "$raw" | tr '[:upper:]' '[:lower:]')"
  case "$normalized" in
    anthropic|claude|claude-opus|claude-sonnet|claude*|opus|sonnet|haiku) normalize_claude_model_alias "$raw" ;;
    gpt|gpt5|gpt-5|gpt5.5|gpt*|openai|codex|*codex*) normalize_gpt_model_alias "$raw" ;;
    *) printf '%s' "$raw" ;;
  esac
}

if [ -n "$CLAUDE_MODEL" ]; then
  CLAUDE_MODEL="$(normalize_model_alias "$CLAUDE_MODEL")"
fi
if [ -n "$CODEX_MODEL" ]; then
  CODEX_MODEL="$(normalize_model_alias "$CODEX_MODEL")"
fi
if [ -n "$CODEX_REVIEW_MODEL" ]; then
  CODEX_REVIEW_MODEL="$(normalize_model_alias "$CODEX_REVIEW_MODEL")"
fi
if [ -n "$VSCODE_CLAUDE_MODEL" ]; then
  VSCODE_CLAUDE_MODEL="$(normalize_model_alias "$VSCODE_CLAUDE_MODEL")"
fi

run() {
  if [ "$DRY_RUN" = true ]; then
    printf '[dry-run] %s\n' "$*"
  else
    "$@"
  fi
}

root_env_run() {
  local env_args=(
    HOME="$AI_TOOL_HOME"
    CODEX_HOME="$CODEX_HOME"
    PATH="$SERVICE_PATH"
    TERM="$AI_SERVICE_TERM"
    AI_RUNNER_PROVIDERS="$AI_RUNNER_PROVIDERS"
    AI_PERMISSION_SCOPE="$AI_PERMISSION_SCOPE"
    AI_REQUIRE_SHELL_CONFIRMATION="$AI_REQUIRE_SHELL_CONFIRMATION"
    AI_PROCESS_CONTROL_ENABLED="$AI_PROCESS_CONTROL_ENABLED"
  )
  if [ "${REQUEST_CLAUDE:-false}" = true ] || [ "${REQUEST_VSCODE_CLAUDE_BACKEND:-false}" = true ]; then
    env_args+=(
      ANTHROPIC_BASE_URL="${ANTHROPIC_BASE_URL:-}"
      ANTHROPIC_AUTH_TOKEN="${ANTHROPIC_AUTH_TOKEN:-}"
      ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-}"
      CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="${CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC:-0}"
      CLAUDE_CODE_ATTRIBUTION_HEADER="${CLAUDE_CODE_ATTRIBUTION_HEADER:-}"
    )
  fi
  if [ "${REQUEST_CODEX:-false}" = true ]; then
    env_args+=(
      OPENAI_API_KEY="${OPENAI_API_KEY:-}"
      CODEX_API_KEY="${CODEX_API_KEY:-}"
      CODEX_BASE_URL="${CODEX_BASE_URL:-}"
      CODEX_OPENAI_BASE_URL="${CODEX_OPENAI_BASE_URL:-}"
    )
  fi
  if [ "$DRY_RUN" = true ]; then
    printf '[dry-run] root env HOME=%s CODEX_HOME=%s PATH=%s %s\n' "$AI_TOOL_HOME" "$CODEX_HOME" "$SERVICE_PATH" "$*"
  else
    sudo env "${env_args[@]}" "$@"
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
    sudo env HOME="$AI_TOOL_HOME" CODEX_HOME="$CODEX_HOME" PATH="$SERVICE_PATH" TERM="$AI_SERVICE_TERM" sh -c "command -v '$1' >/dev/null 2>&1"
  fi
}

codex_installed_version() {
  root_env_run codex --version 2>/dev/null | sed -n 's/.*\([0-9][0-9.]*\).*/\1/p' | head -n 1
}

install_codex_cli() {
  local reason="$1"
  log "$reason; installing $CODEX_NPM_PACKAGE@$CODEX_NPM_VERSION through npm"
  ensure_codex_node
  if [ "$DRY_RUN" = false ]; then
    command -v npm >/dev/null 2>&1 || { log 'npm missing after Node.js install; Codex CLI cannot be installed'; exit 1; }
    sudo npm install -g "$CODEX_NPM_PACKAGE@$CODEX_NPM_VERSION"
    root_has_command codex || { log 'codex npm install did not place codex on the root service PATH'; exit 1; }
    root_env_run codex --version
  else
    log "would run sudo npm install -g $CODEX_NPM_PACKAGE@$CODEX_NPM_VERSION"
  fi
  CODEX_READY=true
  CODEX_STATUS="installed"
  CODEX_REMEDIATION_ZH=""
}

is_truthy() {
  case "${1:-}" in
    1|true|TRUE|yes|YES|on|ON|y|Y) return 0 ;;
    *) return 1 ;;
  esac
}

apt_install() {
  if [ "$DRY_RUN" = true ]; then
    run sudo apt-get install -y "$@"
  else
    sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y "$@"
  fi
}

cc_switch_arch() {
  case "$(uname -m)" in
    x86_64|amd64) printf 'x86_64' ;;
    aarch64|arm64) printf 'arm64' ;;
    *) return 1 ;;
  esac
}

install_cc_switch() {
  log 'stage 02c: install optional CC Switch desktop profile manager'
  if [ "$DRY_RUN" = true ]; then
    log 'would download latest CC Switch Linux .deb from GitHub Releases and install it with apt'
    return 0
  fi
  command -v apt-get >/dev/null 2>&1 || { log 'apt-get is required to install CC Switch .deb on Debian/Ubuntu'; exit 1; }
  command -v python3 >/dev/null 2>&1 || { log 'python3 is required to resolve the latest CC Switch release'; exit 1; }
  command -v curl >/dev/null 2>&1 || { log 'curl is required to download CC Switch'; exit 1; }
  local arch release_info tag url deb_path
  arch="$(cc_switch_arch)" || { log 'CC Switch Linux release supports x86_64 and arm64 only'; exit 1; }
  release_info="$(python3 - "$arch" <<'PY'
import json
import sys
import urllib.request

arch = sys.argv[1]
req = urllib.request.Request(
    "https://api.github.com/repos/farion1231/cc-switch/releases/latest",
    headers={"User-Agent": "FFC-AI-installer"},
)
with urllib.request.urlopen(req, timeout=30) as response:
    data = json.load(response)
suffix = f"Linux-{arch}.deb"
for asset in data.get("assets", []):
    name = str(asset.get("name") or "")
    if name.endswith(suffix):
        print(data.get("tag_name", ""))
        print(asset["browser_download_url"])
        raise SystemExit(0)
raise SystemExit(f"no CC Switch .deb asset found for {arch}")
PY
)"
  tag="$(printf '%s\n' "$release_info" | sed -n '1p')"
  url="$(printf '%s\n' "$release_info" | sed -n '2p')"
  [ -n "$url" ] || { log 'failed to resolve CC Switch .deb download URL'; exit 1; }
  deb_path="/tmp/cc-switch-${tag:-latest}-${arch}.deb"
  log "downloading CC Switch ${tag:-latest} for Linux ${arch}"
  curl -fsSL "$url" -o "$deb_path"
  sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y "$deb_path"
  if command -v dpkg-query >/dev/null 2>&1 && dpkg-query -W -f='${Version}' cc-switch >/dev/null 2>&1; then
    log "CC Switch package installed: $(dpkg-query -W -f='${Version}' cc-switch)"
  elif root_has_command cc-switch; then
    log 'CC Switch binary is installed; skipping --version because it starts the GUI on headless/root servers'
  else
    log 'CC Switch package installed; no cc-switch CLI binary was found. Launch it from the desktop/app menu when using a GUI session.'
  fi
}

write_claude_settings() {
  if [ -z "${ANTHROPIC_BASE_URL:-}" ] &&
    [ -z "${ANTHROPIC_AUTH_TOKEN:-}" ] &&
    [ -z "${ANTHROPIC_API_KEY:-}" ] &&
    [ -z "${CLAUDE_CODE_ATTRIBUTION_HEADER:-}" ] &&
    [ -z "${CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC+x}" ]; then
    return 0
  fi
  log 'stage 02b: write root Claude environment settings'
  if [ "$DRY_RUN" = true ]; then
    log "would write $AI_TOOL_HOME/.claude/settings.json with configured Claude environment variables"
    return 0
  fi
  sudo mkdir -p "$AI_TOOL_HOME/.claude"
  sudo env \
    AI_TOOL_HOME="$AI_TOOL_HOME" \
    ANTHROPIC_BASE_URL="${ANTHROPIC_BASE_URL:-}" \
    ANTHROPIC_AUTH_TOKEN="${ANTHROPIC_AUTH_TOKEN:-}" \
    ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-}" \
    CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="${CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC:-}" \
    CLAUDE_CODE_ATTRIBUTION_HEADER="${CLAUDE_CODE_ATTRIBUTION_HEADER:-}" \
    CLAUDE_REQUEST_TIMEOUT="${CLAUDE_REQUEST_TIMEOUT:-180000}" \
    CLAUDE_MAX_RETRIES="${CLAUDE_MAX_RETRIES:-5}" \
    CLAUDE_STREAM_TIMEOUT="${CLAUDE_STREAM_TIMEOUT:-600000}" \
    python3 - <<'PY'
import json
import os
from pathlib import Path

env = {}
for key in (
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_API_KEY",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC",
    "CLAUDE_CODE_ATTRIBUTION_HEADER",
):
    value = os.environ.get(key, "")
    if value:
        env[key] = value

third_party_api = bool(os.environ.get("ANTHROPIC_BASE_URL", "").strip())
request_timeout = int(os.environ.get("CLAUDE_REQUEST_TIMEOUT", "180000"))
max_retries = int(os.environ.get("CLAUDE_MAX_RETRIES", "5"))
stream_timeout = int(os.environ.get("CLAUDE_STREAM_TIMEOUT", "600000"))

data = {"env": env}
if third_party_api:
    data["thirdPartyApi"] = True
    data["requestTimeout"] = request_timeout
    data["maxRetries"] = max_retries
    data["streamTimeout"] = stream_timeout
    if "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC" not in env:
        env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"

path = Path(os.environ["AI_TOOL_HOME"]) / ".claude" / "settings.json"
path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
path.chmod(0o600)
PY
  sudo chown -R root:root "$AI_TOOL_HOME/.claude" 2>/dev/null || true
}

write_vscode_claude_settings() {
  log 'stage 05b: write VSCode Claude model/API settings for root operation'
  if [ "$DRY_RUN" = true ]; then
    log "would write $AI_TOOL_HOME/.claude/settings.json with VSCode Claude model=$VSCODE_CLAUDE_MODEL"
    return 0
  fi
  sudo mkdir -p "$AI_TOOL_HOME/.claude"
  sudo env \
    AI_TOOL_HOME="$AI_TOOL_HOME" \
    VSCODE_CLAUDE_MODEL="$VSCODE_CLAUDE_MODEL" \
    ANTHROPIC_BASE_URL="${ANTHROPIC_BASE_URL:-}" \
    ANTHROPIC_AUTH_TOKEN="${ANTHROPIC_AUTH_TOKEN:-}" \
    ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-}" \
    CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="${CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC:-1}" \
    CLAUDE_CODE_ATTRIBUTION_HEADER="${CLAUDE_CODE_ATTRIBUTION_HEADER:-0}" \
    CLAUDE_REQUEST_TIMEOUT="${CLAUDE_REQUEST_TIMEOUT:-180000}" \
    CLAUDE_MAX_RETRIES="${CLAUDE_MAX_RETRIES:-5}" \
    CLAUDE_STREAM_TIMEOUT="${CLAUDE_STREAM_TIMEOUT:-600000}" \
    python3 - <<'PY'
import json
import os
from pathlib import Path

path = Path(os.environ["AI_TOOL_HOME"]) / ".claude" / "settings.json"
try:
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
except json.JSONDecodeError:
    data = {}
env = data.get("env") if isinstance(data.get("env"), dict) else {}
env = {str(k): str(v) for k, v in env.items()}
env["CLAUDE_MODEL"] = os.environ["VSCODE_CLAUDE_MODEL"]
for key in (
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_API_KEY",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC",
    "CLAUDE_CODE_ATTRIBUTION_HEADER",
):
    value = os.environ.get(key, "")
    if value:
        env[key] = value

third_party_api = bool(os.environ.get("ANTHROPIC_BASE_URL", "").strip())
if third_party_api:
    data["thirdPartyApi"] = True
    data["requestTimeout"] = int(os.environ.get("CLAUDE_REQUEST_TIMEOUT", "180000"))
    data["maxRetries"] = int(os.environ.get("CLAUDE_MAX_RETRIES", "5"))
    data["streamTimeout"] = int(os.environ.get("CLAUDE_STREAM_TIMEOUT", "600000"))

data["env"] = env
path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
path.chmod(0o600)
PY
  sudo chown -R root:root "$AI_TOOL_HOME/.claude" 2>/dev/null || true
}

write_vscode_root_settings() {
  log 'stage 05c: write VSCode root user settings'
  if [ "$DRY_RUN" = true ]; then
    log "would write $VSCODE_SETTINGS_DIR/settings.json for root VSCode operation"
    return 0
  fi
  sudo mkdir -p "$VSCODE_SETTINGS_DIR"
  sudo env \
    VSCODE_SETTINGS_DIR="$VSCODE_SETTINGS_DIR" \
    python3 - <<'PY'
import json
import os
from pathlib import Path

path = Path(os.environ["VSCODE_SETTINGS_DIR"]) / "settings.json"
try:
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
except json.JSONDecodeError:
    data = {}
data.setdefault("security.workspace.trust.enabled", False)
data.setdefault("telemetry.telemetryLevel", "off")
path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
path.chmod(0o600)
PY
  sudo chown -R root:root "$VSCODE_ROOT_DIR" 2>/dev/null || true
}

should_write_claude_settings() {
  if [ "$REQUEST_CLAUDE" != true ]; then
    return 1
  fi
  case "$AI_WRITE_CLAUDE_SETTINGS" in
    1|true|yes|on) return 0 ;;
    0|false|no|off) return 1 ;;
    auto|"") [ "$REQUEST_CLAUDE" = true ] ;;
    *) log 'AI_WRITE_CLAUDE_SETTINGS must be auto, true, or false'; exit 2 ;;
  esac
}

remove_if_exists() {
  local path="$1"
  if [ "$DRY_RUN" = true ]; then
    log "would remove stale unrequested provider config $path if it exists"
  elif [ -e "$path" ]; then
    sudo rm -f "$path"
    log "removed stale unrequested provider config $path"
  fi
}

cleanup_unrequested_provider_configs() {
  if [ "$REQUEST_CLAUDE" != true ]; then
    if [ "${REQUEST_VSCODE:-false}" = true ]; then
      log 'preserve root Claude settings because VSCode is configured to use Claude model/API settings'
    else
      remove_if_exists "$AI_TOOL_HOME/.claude/settings.json"
    fi
    remove_if_exists "$AI_TOOL_HOME/.anthropic-api-key"
  fi
  if [ "$REQUEST_CODEX" != true ]; then
    remove_if_exists "$CODEX_HOME/config.toml"
    remove_if_exists "$CODEX_HOME/auth.json"
  fi
}

vscode_root_safe_version() {
  if [ "$DRY_RUN" = true ]; then
    log 'would verify code --version with root-safe --no-sandbox and dedicated user-data/extensions directories'
    return 0
  fi
  sudo mkdir -p "$VSCODE_ROOT_DIR/extensions"
  root_env_run code \
    --no-sandbox \
    --user-data-dir="$VSCODE_ROOT_DIR" \
    --extensions-dir="$VSCODE_ROOT_DIR/extensions" \
    --disable-workspace-trust \
    --version | head -n 1 || true
}

install_vscode() {
  log 'stage 05: install or verify VSCode for root/full-access operation'
  VSCODE_READY=false
  if root_has_command code; then
    vscode_root_safe_version
  elif command -v apt-get >/dev/null 2>&1; then
    log 'code missing; installing latest Visual Studio Code from Microsoft apt repository'
    run sudo install -d -m 0755 /etc/apt/keyrings
    if [ "$DRY_RUN" = false ]; then
      curl -fsSL https://packages.microsoft.com/keys/microsoft.asc -o /tmp/microsoft.asc
      command -v gpg >/dev/null 2>&1 || { log 'gpg required for Microsoft apt key installation'; exit 1; }
      rm -f /tmp/packages.microsoft.gpg
      gpg --dearmor -o /tmp/packages.microsoft.gpg /tmp/microsoft.asc
      sudo install -m 0644 /tmp/packages.microsoft.gpg /etc/apt/keyrings/packages.microsoft.gpg
      echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/packages.microsoft.gpg] https://packages.microsoft.com/repos/code stable main" \
        | sudo tee /etc/apt/sources.list.d/vscode.list >/dev/null
    else
      log 'would install Microsoft apt key and /etc/apt/sources.list.d/vscode.list'
    fi
    run sudo apt-get update
    apt_install code
    if [ "$DRY_RUN" = false ]; then
      root_has_command code || { log 'VSCode install failed; code command is required in the root service PATH'; exit 1; }
      vscode_root_safe_version
    else
      vscode_root_safe_version
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
  local major min_major install_major setup_script tool_label
  tool_label="${1:-Codex CLI}"
  min_major="${CODEX_NODE_MIN_MAJOR:-$(read_lock codex_node_min_major || true)}"
  install_major="${CODEX_NODE_INSTALL_MAJOR:-$(read_lock codex_node_install_major || true)}"
  min_major="${min_major:-22}"
  install_major="${install_major:-24}"
  case "$min_major" in
    ''|*[!0-9]*) log 'codex_node_min_major must be numeric'; exit 2 ;;
  esac
  case "$install_major" in
    ''|*[!0-9]*) log 'codex_node_install_major must be numeric'; exit 2 ;;
  esac
  major="$(node_major || true)"
  case "$major" in
    ''|*[!0-9]*) major=0 ;;
  esac
  if [ "$major" -ge "$min_major" ] && [ $((major % 2)) -eq 0 ]; then
    return 0
  fi
  if ! command -v apt-get >/dev/null 2>&1; then
    log "Node.js ${min_major}+ even-major stable/LTS is required for ${tool_label}; install Node.js ${install_major}.x LTS and npm before rerunning"
    return 1
  fi
  log "Node.js ${min_major}+ even-major stable/LTS is required for ${tool_label}; installing Node.js ${install_major}.x LTS from NodeSource"
  setup_script="/tmp/nodesource_setup_${install_major}.x.sh"
  if [ "$DRY_RUN" = false ]; then
    curl -fsSL "https://deb.nodesource.com/setup_${install_major}.x" -o "$setup_script"
    sudo env DEBIAN_FRONTEND=noninteractive bash "$setup_script"
  else
    log "would download and run https://deb.nodesource.com/setup_${install_major}.x"
  fi
  apt_install nodejs
  major="$(node_major || true)"
  case "$major" in
    ''|*[!0-9]*) major=0 ;;
  esac
  if [ "$DRY_RUN" = false ] && { [ "$major" -lt "$min_major" ] || [ $((major % 2)) -ne 0 ]; }; then
    log "Node.js ${min_major}+ even-major stable/LTS install failed; ${tool_label} cannot be installed safely"
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

REQUEST_CLAUDE=false
REQUEST_CODEX=false
REQUEST_VSCODE=false
REQUEST_VSCODE_CLAUDE_BACKEND=false
REQUEST_CC_SWITCH=false
REQUEST_RUNNER=false
REQUEST_TELEGRAM=false
if [ -z "$AI_RUNNER_COMPONENTS" ]; then
  log 'AI_RUNNER_COMPONENTS is required so the installer knows which AI tools and channels to prepare.'
  usage
  exit 2
fi
IFS=',' read -r -a REQUESTED_COMPONENTS_ARRAY <<< "$AI_RUNNER_COMPONENTS"
for raw_component in "${REQUESTED_COMPONENTS_ARRAY[@]}"; do
  component="$(printf '%s' "$raw_component" | tr -d '[:space:]')"
  case "$component" in
    claude|claude-code)
      REQUEST_CLAUDE=true
      REQUEST_RUNNER=true
      ;;
    codex)
      REQUEST_CODEX=true
      REQUEST_RUNNER=true
      ;;
    vscode|code)
      REQUEST_VSCODE=true
      ;;
    runner)
      REQUEST_RUNNER=true
      ;;
    telegram)
      ENABLE_TELEGRAM=true
      REQUEST_TELEGRAM=true
      REQUEST_RUNNER=true
      ;;
    cc-switch|ccswitch)
      REQUEST_CC_SWITCH=true
      ;;
    all|full|core)
      REQUEST_CLAUDE=true
      REQUEST_CODEX=true
      REQUEST_VSCODE=true
      REQUEST_RUNNER=true
      ;;
    *)
      log "AI_RUNNER_COMPONENTS contains unsupported component: $component"
      exit 2
      ;;
  esac
done
if is_truthy "$AI_INSTALL_CC_SWITCH"; then
  REQUEST_CC_SWITCH=true
fi
if [ "$REQUEST_RUNNER" = true ] && [ "$REQUEST_CLAUDE" != true ] && [ "$REQUEST_CODEX" != true ] && [ "$REQUEST_VSCODE" != true ]; then
  log 'runner/telegram selected without an AI provider; installing management-only runner commands without Claude Code, Codex, or VSCode backend'
fi
if [ "$REQUEST_VSCODE" = true ] && [ "$REQUEST_RUNNER" = true ]; then
  REQUEST_VSCODE_CLAUDE_BACKEND=true
fi
append_runner_provider() {
  local provider="$1"
  case ",$AI_RUNNER_PROVIDERS," in
    *",$provider,"*) return 0 ;;
  esac
  AI_RUNNER_PROVIDERS="${AI_RUNNER_PROVIDERS:+$AI_RUNNER_PROVIDERS,}$provider"
}
AI_RUNNER_PROVIDERS=""
if [ "$REQUEST_CODEX" = true ]; then
  append_runner_provider codex
fi
if [ "$REQUEST_CLAUDE" = true ]; then
  append_runner_provider claude-code
fi
if [ "$REQUEST_VSCODE_CLAUDE_BACKEND" = true ]; then
  append_runner_provider vscode
fi
AI_ADAPTER_TYPE="management"
RUNNER_PROVIDER_COUNT=0
[ -n "$AI_RUNNER_PROVIDERS" ] && RUNNER_PROVIDER_COUNT="$(printf '%s' "$AI_RUNNER_PROVIDERS" | awk -F, '{print NF}')"
if [ "$RUNNER_PROVIDER_COUNT" -gt 1 ]; then
  AI_ADAPTER_TYPE="multi"
elif [ "$RUNNER_PROVIDER_COUNT" -eq 1 ]; then
  AI_ADAPTER_TYPE="$AI_RUNNER_PROVIDERS"
fi
if [ "$REQUEST_VSCODE" = true ] && [ "$REQUEST_RUNNER" != true ]; then
  AI_ADAPTER_TYPE="vscode"
fi
if [ -z "$AI_DEFAULT_PROVIDER" ]; then
  case ",$AI_RUNNER_PROVIDERS," in
    *,codex,*) AI_DEFAULT_PROVIDER="codex" ;;
    *,claude-code,*) AI_DEFAULT_PROVIDER="claude-code" ;;
    *,vscode,*) AI_DEFAULT_PROVIDER="vscode" ;;
  esac
fi
if [ "$REQUEST_RUNNER" = true ] && [ -n "$AI_DEFAULT_PROVIDER" ]; then
  AI_DEFAULT_PROVIDER="$(normalize_provider "$AI_DEFAULT_PROVIDER")" || { log 'AI_DEFAULT_PROVIDER must be claude-code, claude, vscode, code, or codex'; exit 2; }
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

validate_secret_value() {
  local name="$1"
  local value="${!name:-}"
  if [ -z "$value" ]; then
    return 0
  fi
  case "$value" in
    *[[:space:]]*) log "$name must not contain whitespace"; exit 2 ;;
  esac
}

validate_http_url_value() {
  local name="$1"
  local value="${!name:-}"
  if [ -z "$value" ]; then
    return 0
  fi
  case "$value" in
    http://*|https://*) ;;
    *) log "$name must be an http(s) URL"; exit 2 ;;
  esac
  case "$value" in
    *[[:space:]\"\'\\]*) log "$name must not contain whitespace, quotes, or backslashes"; exit 2 ;;
  esac
}

validate_codex_model_provider_value() {
  local value="${CODEX_MODEL_PROVIDER:-}"
  if [ -z "$value" ]; then
    return 0
  fi
  case "$value" in
    *[!A-Za-z0-9_-]*) log 'CODEX_MODEL_PROVIDER must contain only letters, numbers, underscores, or hyphens'; exit 2 ;;
  esac
  case "$value" in
    ollama|lmstudio) log 'CODEX_MODEL_PROVIDER cannot override reserved built-in provider ids ollama or lmstudio'; exit 2 ;;
  esac
}

validate_provider_runtime_config() {
  validate_secret_value OPENAI_API_KEY
  validate_secret_value CODEX_API_KEY
  validate_secret_value ANTHROPIC_AUTH_TOKEN
  validate_secret_value ANTHROPIC_API_KEY
  validate_http_url_value CODEX_BASE_URL
  validate_http_url_value CODEX_OPENAI_BASE_URL
  validate_http_url_value ANTHROPIC_BASE_URL
  validate_codex_model_provider_value
  if [ "$REQUEST_CODEX" = true ]; then
    local codex_key="${CODEX_API_KEY:-${OPENAI_API_KEY:-}}"
    case "${codex_key,,}" in
      sk-ant-*) log 'Codex/OpenAI config cannot use an Anthropic sk-ant-* key; use ANTHROPIC_AUTH_TOKEN for claude-code/vscode.'; exit 2 ;;
    esac
  fi
}

validate_provider_runtime_config

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

log 'stage 01b: remove stale provider configs for unrequested AI tools'
cleanup_unrequested_provider_configs

log 'stage 02: install system packages required by runner'
if command -v apt-get >/dev/null 2>&1; then
  run sudo apt-get update
  apt_install python3 python3-pip python3-venv ca-certificates curl git openssl gpg bubblewrap
else
  log 'apt-get unavailable; package installation skipped and must be handled externally'
fi
if [ "$REQUEST_CC_SWITCH" = true ]; then
  install_cc_switch
else
  log 'stage 02c: skip CC Switch because it is optional and not requested'
fi
if should_write_claude_settings; then
  write_claude_settings
elif [ "$REQUEST_VSCODE_CLAUDE_BACKEND" = true ]; then
  log 'stage 02b: defer Claude environment settings to VSCode Claude backend writer'
elif [ -n "${ANTHROPIC_BASE_URL:-}" ] || [ -n "${ANTHROPIC_AUTH_TOKEN:-}" ] || [ -n "${ANTHROPIC_API_KEY:-}" ] || [ -n "${CLAUDE_CODE_ATTRIBUTION_HEADER:-}" ]; then
  log 'stage 02b: skip Claude environment settings because Claude Code is not requested'
fi

log 'stage 03: install or verify requested Claude Code provider/backend'
CLAUDE_NPM_PACKAGE="$(read_lock claude_code_npm_package || true)"
CLAUDE_NPM_VERSION="$(read_lock claude_code_npm_version || true)"
CLAUDE_NPM_PACKAGE="${CLAUDE_NPM_PACKAGE:-@anthropic-ai/claude-code}"
CLAUDE_NPM_VERSION="${CLAUDE_NPM_VERSION:-2.1.153}"
CLAUDE_READY=false
if [ "$REQUEST_CLAUDE" = true ] || [ "$REQUEST_VSCODE_CLAUDE_BACKEND" = true ]; then
  if root_has_command claude; then
    root_env_run claude --version
    CLAUDE_READY=true
  elif command -v apt-get >/dev/null 2>&1; then
    log "claude missing; installing $CLAUDE_NPM_PACKAGE@$CLAUDE_NPM_VERSION through npm"
    ensure_codex_node "Claude Code"
    if [ "$DRY_RUN" = false ]; then
      command -v npm >/dev/null 2>&1 || { log 'npm missing after Node.js install; Claude Code cannot be installed'; exit 1; }
      sudo npm install -g "$CLAUDE_NPM_PACKAGE@$CLAUDE_NPM_VERSION"
      root_has_command claude || { log 'claude npm install did not place claude on the root service PATH'; exit 1; }
      root_env_run claude --version
      CLAUDE_READY=true
    else
      log "would run sudo npm install -g $CLAUDE_NPM_PACKAGE@$CLAUDE_NPM_VERSION"
    fi
  else
    log 'claude missing and native installer unavailable; install from official Claude Code docs before core_ready'
    exit 1
  fi
else
  log 'stage 03: skip Claude Code provider because AI_RUNNER_COMPONENTS does not request it'
fi

log 'stage 04: install or verify requested Codex CLI provider'
CODEX_NPM_PACKAGE="$(read_lock codex_npm_package || true)"
CODEX_NPM_VERSION="$(read_lock codex_npm_version || true)"
CODEX_NPM_PACKAGE="${CODEX_NPM_PACKAGE:-@openai/codex}"
CODEX_NPM_VERSION="${CODEX_NPM_VERSION:-0.142.0}"
CODEX_READY=false
CODEX_STATUS="install_required"
CODEX_REMEDIATION_ZH="Codex CLI 必须由安装脚本全局安装；若失败请检查网络、Node.js/npm、以及版本锁。"
CODEX_EXEC_JSON_AVAILABLE=false
CODEX_EXEC_EPHEMERAL_AVAILABLE=false
CODEX_EXEC_RESUME_AVAILABLE=false
CODEX_EXEC_RESUME_JSON_AVAILABLE=false
CODEX_EXEC_RESUME_OUTPUT_LAST_MESSAGE_AVAILABLE=false
CODEX_EXEC_CD_AVAILABLE=false
CODEX_EXEC_OUTPUT_LAST_MESSAGE_AVAILABLE=false
CODEX_EXEC_ADD_DIR_AVAILABLE=false
CODEX_EXEC_SKIP_GIT_REPO_CHECK_AVAILABLE=false
CODEX_EXEC_FULL_ACCESS_MODE="unavailable"
CODEX_TELEGRAM_REALTIME_STATUS=false
if [ "$REQUEST_CODEX" = true ]; then
  if root_has_command codex; then
    CODEX_INSTALLED_VERSION="$(codex_installed_version || true)"
    if [ "$DRY_RUN" = true ] || [ "$CODEX_INSTALLED_VERSION" = "$CODEX_NPM_VERSION" ]; then
      root_env_run codex --version
      CODEX_READY=true
      CODEX_STATUS="installed"
      CODEX_REMEDIATION_ZH=""
    else
      install_codex_cli "codex version ${CODEX_INSTALLED_VERSION:-unknown} does not match locked stable version $CODEX_NPM_VERSION"
    fi
  elif command -v apt-get >/dev/null 2>&1 || command -v npm >/dev/null 2>&1; then
    install_codex_cli "codex missing"
  else
    log 'codex missing and native package installer unavailable; Codex CLI is required for core install'
    exit 1
  fi
else
  log 'stage 04: skip Codex CLI provider because AI_RUNNER_COMPONENTS does not request it'
fi
if [ "$REQUEST_CODEX" = true ] && { [ -n "${CODEX_API_KEY:-}" ] || [ -n "${OPENAI_API_KEY:-}" ]; }; then
  CODEX_KEY="${CODEX_API_KEY:-$OPENAI_API_KEY}"

  # 确定有效的BASE URL（优先级：CODEX_OPENAI_BASE_URL > CODEX_BASE_URL > 默认值）
  if [ -n "${CODEX_OPENAI_BASE_URL:-}" ]; then
    CODEX_EFFECTIVE_BASE_URL="$CODEX_OPENAI_BASE_URL"
  elif [ -n "${CODEX_BASE_URL:-}" ]; then
    CODEX_EFFECTIVE_BASE_URL="$CODEX_BASE_URL"
  else
    CODEX_EFFECTIVE_BASE_URL="https://api.openai.com/v1"
  fi
  CODEX_EFFECTIVE_MODEL_PROVIDER="${CODEX_MODEL_PROVIDER:-openai}"
  if [ "$CODEX_EFFECTIVE_BASE_URL" != "https://api.openai.com/v1" ] && [ "$CODEX_EFFECTIVE_MODEL_PROVIDER" = "openai" ]; then
    CODEX_EFFECTIVE_MODEL_PROVIDER="$CODEX_OPENAI_COMPAT_PROVIDER"
  fi

  if [ "$DRY_RUN" = false ]; then
    sudo mkdir -p "$CODEX_HOME"
    CODEX_OPENAI_BASE_URL_CONFIG_LINE=""
    if [ "$CODEX_EFFECTIVE_MODEL_PROVIDER" = "openai" ]; then
      CODEX_OPENAI_BASE_URL_CONFIG_LINE="openai_base_url = \"$CODEX_EFFECTIVE_BASE_URL\""
    fi
    sudo tee "$CODEX_HOME/config.toml" >/dev/null <<EOF
model_provider = "$CODEX_EFFECTIVE_MODEL_PROVIDER"
model = "${CODEX_MODEL:-gpt-5.5}"
review_model = "${CODEX_REVIEW_MODEL:-${CODEX_MODEL:-gpt-5.5}}"
model_reasoning_effort = "${CODEX_REASONING_EFFORT:-xhigh}"
approval_policy = "never"
sandbox_mode = "danger-full-access"
model_context_window = ${CODEX_CONTEXT_WINDOW:-200000}
model_auto_compact_token_limit = ${CODEX_AUTO_COMPACT_TOKEN_LIMIT:-160000}
hide_agent_reasoning = false
$CODEX_OPENAI_BASE_URL_CONFIG_LINE

[shell_environment_policy]
inherit = "all"

[sandbox_workspace_write]
network_access = true

[features]
goals = true
EOF
    if [ "$CODEX_EFFECTIVE_MODEL_PROVIDER" != "openai" ]; then
      sudo tee -a "$CODEX_HOME/config.toml" >/dev/null <<EOF

[model_providers.$CODEX_EFFECTIVE_MODEL_PROVIDER]
name = "$CODEX_EFFECTIVE_MODEL_PROVIDER"
base_url = "$CODEX_EFFECTIVE_BASE_URL"
wire_api = "responses"
env_key = "OPENAI_API_KEY"
supports_websockets = false
request_max_retries = 6
stream_max_retries = 10
stream_idle_timeout_ms = 600000
EOF
    fi
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
elif [ "$REQUEST_CODEX" != true ] && { [ -n "${CODEX_API_KEY:-}" ] || [ -n "${OPENAI_API_KEY:-}" ]; }; then
  log 'stage 04b: skip Codex config/auth because Codex is not requested'
fi

VSCODE_READY=false
if [ "$REQUEST_VSCODE" = true ]; then
  install_vscode
  write_vscode_root_settings
  write_vscode_claude_settings
else
  log 'stage 05: skip VSCode because AI_RUNNER_COMPONENTS does not request it'
fi

if [ "$REQUEST_RUNNER" != true ]; then
  log 'runner bridge/provider service install skipped because AI_RUNNER_COMPONENTS does not request runner, claude-code, codex, or telegram'
  if [ "$DRY_RUN" = false ]; then
    sudo mkdir -p "$STATE_ROOT"
    sudo tee "$STATE_ROOT/install-manifest.json" >/dev/null <<EOF
{
  "component": "ai-tool-components",
  "requested_components": "$AI_RUNNER_COMPONENTS",
  "adapter_type": "$AI_ADAPTER_TYPE",
  "runner_enabled": false,
  "ai_tool_home": "$AI_TOOL_HOME",
  "vscode_ready": $VSCODE_READY,
  "vscode_root_wrapper": "$VSCODE_ROOT_WRAPPER",
  "vscode_root_dir": "$VSCODE_ROOT_DIR",
  "vscode_settings_dir": "$VSCODE_SETTINGS_DIR"
}
EOF
    sudo chmod 0600 "$STATE_ROOT/install-manifest.json"
  else
    log "would write $STATE_ROOT/install-manifest.json for component-only install"
  fi
  exit 0
fi

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
  PREVIOUS_TELEGRAM_STATUS_INTERVAL_SECONDS="$(config_value TELEGRAM_STATUS_INTERVAL_SECONDS)"
  PREVIOUS_TELEGRAM_STATUS_MIN_UPDATE_SECONDS="$(config_value TELEGRAM_STATUS_MIN_UPDATE_SECONDS)"
  PREVIOUS_TELEGRAM_CLEAR_WEBHOOK_ON_STARTUP="$(config_value TELEGRAM_CLEAR_WEBHOOK_ON_STARTUP)"
  PREVIOUS_TELEGRAM_SYNC_COMMANDS_ON_STARTUP="$(config_value TELEGRAM_SYNC_COMMANDS_ON_STARTUP)"
  PREVIOUS_TELEGRAM_ALLOWED_UPDATES="$(config_value TELEGRAM_ALLOWED_UPDATES)"
  PREVIOUS_TELEGRAM_NATIVE_DRAFT_PROGRESS="$(config_value TELEGRAM_NATIVE_DRAFT_PROGRESS)"
  PREVIOUS_TELEGRAM_NATIVE_DRAFT_ALLOW_CHAT_IDS="$(config_value TELEGRAM_NATIVE_DRAFT_ALLOW_CHAT_IDS)"
  PREVIOUS_TELEGRAM_GROUP_MODE="$(config_value TELEGRAM_GROUP_MODE)"
  PREVIOUS_AI_LOCAL_EXEC_TIMEOUT_SECONDS="$(config_value AI_LOCAL_EXEC_TIMEOUT_SECONDS)"
  PREVIOUS_AI_LOCAL_EXEC_MAX_OUTPUT_BYTES="$(config_value AI_LOCAL_EXEC_MAX_OUTPUT_BYTES)"
  PREVIOUS_AI_PROCESS_CONTROL_ENABLED="$(config_value AI_PROCESS_CONTROL_ENABLED)"
  PREVIOUS_AI_TASK_RESERVED_USD="$(config_value AI_TASK_RESERVED_USD)"
  PREVIOUS_AI_TASK_TIMEOUT_SECONDS="$(config_value AI_TASK_TIMEOUT_SECONDS)"
  PREVIOUS_CLAUDE_MAX_TURNS="$(config_value CLAUDE_MAX_TURNS)"
  PREVIOUS_CLAUDE_API_RETRY_ATTEMPTS="$(config_value CLAUDE_API_RETRY_ATTEMPTS)"
  PREVIOUS_CLAUDE_API_RETRY_SLEEP_SECONDS="$(config_value CLAUDE_API_RETRY_SLEEP_SECONDS)"
  PREVIOUS_VSCODE_CLAUDE_MAX_TURNS="$(config_value VSCODE_CLAUDE_MAX_TURNS)"
  PREVIOUS_VSCODE_CLAUDE_API_RETRY_ATTEMPTS="$(config_value VSCODE_CLAUDE_API_RETRY_ATTEMPTS)"
  PREVIOUS_VSCODE_CLAUDE_API_RETRY_SLEEP_SECONDS="$(config_value VSCODE_CLAUDE_API_RETRY_SLEEP_SECONDS)"
  PREVIOUS_CODEX_EXEC_EPHEMERAL="$(config_value CODEX_EXEC_EPHEMERAL)"
  EFFECTIVE_AI_TASK_RESERVED_USD="${AI_TASK_RESERVED_USD:-${PREVIOUS_AI_TASK_RESERVED_USD:-0}}"
  EFFECTIVE_AI_TASK_TIMEOUT_SECONDS="${AI_TASK_TIMEOUT_SECONDS:-${PREVIOUS_AI_TASK_TIMEOUT_SECONDS:-7200}}"
  EFFECTIVE_CLAUDE_MAX_TURNS="${CLAUDE_MAX_TURNS:-${PREVIOUS_CLAUDE_MAX_TURNS:-0}}"
  EFFECTIVE_CLAUDE_API_RETRY_ATTEMPTS="${CLAUDE_API_RETRY_ATTEMPTS:-${PREVIOUS_CLAUDE_API_RETRY_ATTEMPTS:-3}}"
  EFFECTIVE_CLAUDE_API_RETRY_SLEEP_SECONDS="${CLAUDE_API_RETRY_SLEEP_SECONDS:-${PREVIOUS_CLAUDE_API_RETRY_SLEEP_SECONDS:-12}}"
  EFFECTIVE_VSCODE_CLAUDE_MAX_TURNS="${VSCODE_CLAUDE_MAX_TURNS:-${PREVIOUS_VSCODE_CLAUDE_MAX_TURNS:-0}}"
  EFFECTIVE_VSCODE_CLAUDE_API_RETRY_ATTEMPTS="${VSCODE_CLAUDE_API_RETRY_ATTEMPTS:-${PREVIOUS_VSCODE_CLAUDE_API_RETRY_ATTEMPTS:-3}}"
  EFFECTIVE_VSCODE_CLAUDE_API_RETRY_SLEEP_SECONDS="${VSCODE_CLAUDE_API_RETRY_SLEEP_SECONDS:-${PREVIOUS_VSCODE_CLAUDE_API_RETRY_SLEEP_SECONDS:-12}}"
  EFFECTIVE_CODEX_EXEC_EPHEMERAL="${CODEX_EXEC_EPHEMERAL:-0}"
  EFFECTIVE_AI_PROCESS_CONTROL_ENABLED="${AI_PROCESS_CONTROL_ENABLED:-${PREVIOUS_AI_PROCESS_CONTROL_ENABLED:-1}}"
  sudo tee "$STATE_ROOT/config.env" >/dev/null <<EOF
AI_REMOTE_STATE=$STATE_ROOT
AI_WORKSPACE_ROOT=$WORKSPACE_ROOT
AI_ADAPTER_TYPE=$AI_ADAPTER_TYPE
AI_RUNNER_PROVIDERS=$AI_RUNNER_PROVIDERS
AI_PERMISSION_SCOPE=$AI_PERMISSION_SCOPE
AI_REQUIRE_SHELL_CONFIRMATION=$AI_REQUIRE_SHELL_CONFIRMATION
AI_PROCESS_CONTROL_ENABLED=$EFFECTIVE_AI_PROCESS_CONTROL_ENABLED
HOME=$AI_TOOL_HOME
CODEX_HOME=$CODEX_HOME
PATH=$SERVICE_PATH
TERM=$AI_SERVICE_TERM
AI_TASK_RESERVED_USD=$EFFECTIVE_AI_TASK_RESERVED_USD
AI_TASK_TIMEOUT_SECONDS=$EFFECTIVE_AI_TASK_TIMEOUT_SECONDS
AI_BRIDGE_SHARED_SECRET=$BRIDGE_SECRET
EOF
  if [ "$REQUEST_CLAUDE" = true ]; then
    printf 'CLAUDE_MODEL=%s\n' "$CLAUDE_MODEL" | sudo tee -a "$STATE_ROOT/config.env" >/dev/null
    printf 'CLAUDE_MAX_TURNS=%s\n' "$EFFECTIVE_CLAUDE_MAX_TURNS" | sudo tee -a "$STATE_ROOT/config.env" >/dev/null
    printf 'CLAUDE_API_RETRY_ATTEMPTS=%s\n' "$EFFECTIVE_CLAUDE_API_RETRY_ATTEMPTS" | sudo tee -a "$STATE_ROOT/config.env" >/dev/null
    printf 'CLAUDE_API_RETRY_SLEEP_SECONDS=%s\n' "$EFFECTIVE_CLAUDE_API_RETRY_SLEEP_SECONDS" | sudo tee -a "$STATE_ROOT/config.env" >/dev/null
  fi
  if [ "$REQUEST_VSCODE" = true ]; then
    printf 'VSCODE_CLAUDE_MODEL=%s\n' "$VSCODE_CLAUDE_MODEL" | sudo tee -a "$STATE_ROOT/config.env" >/dev/null
    printf 'VSCODE_CLAUDE_MAX_TURNS=%s\n' "$EFFECTIVE_VSCODE_CLAUDE_MAX_TURNS" | sudo tee -a "$STATE_ROOT/config.env" >/dev/null
    printf 'VSCODE_CLAUDE_API_RETRY_ATTEMPTS=%s\n' "$EFFECTIVE_VSCODE_CLAUDE_API_RETRY_ATTEMPTS" | sudo tee -a "$STATE_ROOT/config.env" >/dev/null
    printf 'VSCODE_CLAUDE_API_RETRY_SLEEP_SECONDS=%s\n' "$EFFECTIVE_VSCODE_CLAUDE_API_RETRY_SLEEP_SECONDS" | sudo tee -a "$STATE_ROOT/config.env" >/dev/null
  fi
  if [ "$REQUEST_CLAUDE" = true ] || [ "$REQUEST_VSCODE_CLAUDE_BACKEND" = true ]; then
    if [ -n "${CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC+x}" ]; then
      printf 'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=%s\n' "${CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC:-0}" | sudo tee -a "$STATE_ROOT/config.env" >/dev/null
    fi
    if [ -n "${CLAUDE_CODE_ATTRIBUTION_HEADER:-}" ]; then
      printf 'CLAUDE_CODE_ATTRIBUTION_HEADER=%s\n' "$CLAUDE_CODE_ATTRIBUTION_HEADER" | sudo tee -a "$STATE_ROOT/config.env" >/dev/null
    fi
    if [ -n "${ANTHROPIC_BASE_URL:-}" ]; then
    printf 'ANTHROPIC_BASE_URL=%s\n' "$ANTHROPIC_BASE_URL" | sudo tee -a "$STATE_ROOT/config.env" >/dev/null
    fi
    if [ -n "${ANTHROPIC_AUTH_TOKEN:-}" ]; then
    printf 'ANTHROPIC_AUTH_TOKEN=%s\n' "$ANTHROPIC_AUTH_TOKEN" | sudo tee -a "$STATE_ROOT/config.env" >/dev/null
    fi
    if [ -n "${ANTHROPIC_API_KEY:-}" ]; then
      printf 'ANTHROPIC_API_KEY=%s\n' "$ANTHROPIC_API_KEY" | sudo tee -a "$STATE_ROOT/config.env" >/dev/null
    fi
  fi
  if [ "$REQUEST_CODEX" = true ] && { [ -n "${CODEX_API_KEY:-}" ] || [ -n "${OPENAI_API_KEY:-}" ]; }; then
    printf 'OPENAI_API_KEY=%s\n' "${CODEX_API_KEY:-$OPENAI_API_KEY}" | sudo tee -a "$STATE_ROOT/config.env" >/dev/null
  fi
  if [ "$REQUEST_CODEX" = true ]; then
    printf 'CODEX_MODEL=%s\n' "${CODEX_MODEL:-gpt-5.5}" | sudo tee -a "$STATE_ROOT/config.env" >/dev/null
    printf 'CODEX_MODEL_PROVIDER=%s\n' "${CODEX_EFFECTIVE_MODEL_PROVIDER:-openai}" | sudo tee -a "$STATE_ROOT/config.env" >/dev/null
    printf 'CODEX_EXEC_EPHEMERAL=%s\n' "$EFFECTIVE_CODEX_EXEC_EPHEMERAL" | sudo tee -a "$STATE_ROOT/config.env" >/dev/null
  fi
  if [ "$REQUEST_CODEX" = true ] && { [ -n "${CODEX_OPENAI_BASE_URL:-}" ] || [ -n "${CODEX_BASE_URL:-}" ]; }; then
    printf 'CODEX_BASE_URL=%s\n' "${CODEX_OPENAI_BASE_URL:-$CODEX_BASE_URL}" | sudo tee -a "$STATE_ROOT/config.env" >/dev/null
  fi
  for key in MATTERMOST_PLATFORM_URL MATTERMOST_WEBHOOK_URL MATTERMOST_BOT_TOKEN MATTERMOST_SLASH_TOKEN AI_BRIDGE_SECRET_TRANSFER_METHOD TELEGRAM_BOT_TOKEN TELEGRAM_ALLOWED_CHAT_IDS TELEGRAM_API_BASE TELEGRAM_RESERVED_USD TELEGRAM_STATUS_INTERVAL_SECONDS TELEGRAM_STATUS_MIN_UPDATE_SECONDS TELEGRAM_CLEAR_WEBHOOK_ON_STARTUP TELEGRAM_SYNC_COMMANDS_ON_STARTUP TELEGRAM_ALLOWED_UPDATES TELEGRAM_NATIVE_DRAFT_PROGRESS TELEGRAM_NATIVE_DRAFT_ALLOW_CHAT_IDS TELEGRAM_GROUP_MODE AI_LOCAL_EXEC_TIMEOUT_SECONDS AI_LOCAL_EXEC_MAX_OUTPUT_BYTES; do
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
        TELEGRAM_RESERVED_USD) value="${PREVIOUS_TELEGRAM_RESERVED_USD:-$EFFECTIVE_AI_TASK_RESERVED_USD}" ;;
        TELEGRAM_STATUS_INTERVAL_SECONDS) value="${PREVIOUS_TELEGRAM_STATUS_INTERVAL_SECONDS:-5}" ;;
        TELEGRAM_STATUS_MIN_UPDATE_SECONDS) value="${PREVIOUS_TELEGRAM_STATUS_MIN_UPDATE_SECONDS:-0.8}" ;;
        TELEGRAM_CLEAR_WEBHOOK_ON_STARTUP) value="${PREVIOUS_TELEGRAM_CLEAR_WEBHOOK_ON_STARTUP:-1}" ;;
        TELEGRAM_SYNC_COMMANDS_ON_STARTUP) value="${PREVIOUS_TELEGRAM_SYNC_COMMANDS_ON_STARTUP:-1}" ;;
        TELEGRAM_ALLOWED_UPDATES) value="${PREVIOUS_TELEGRAM_ALLOWED_UPDATES:-message,edited_message,callback_query}" ;;
        TELEGRAM_NATIVE_DRAFT_PROGRESS) value="${PREVIOUS_TELEGRAM_NATIVE_DRAFT_PROGRESS:-0}" ;;
        TELEGRAM_NATIVE_DRAFT_ALLOW_CHAT_IDS) value="$PREVIOUS_TELEGRAM_NATIVE_DRAFT_ALLOW_CHAT_IDS" ;;
        TELEGRAM_GROUP_MODE) value="${PREVIOUS_TELEGRAM_GROUP_MODE:-mention}" ;;
        AI_LOCAL_EXEC_TIMEOUT_SECONDS) value="${PREVIOUS_AI_LOCAL_EXEC_TIMEOUT_SECONDS:-300}" ;;
        AI_LOCAL_EXEC_MAX_OUTPUT_BYTES) value="${PREVIOUS_AI_LOCAL_EXEC_MAX_OUTPUT_BYTES:-120000}" ;;
      esac
    fi
    if [ -n "$value" ]; then
      printf '%s=%s\n' "$key" "$value" | sudo tee -a "$STATE_ROOT/config.env" >/dev/null
    fi
  done
  sudo chmod 0600 "$STATE_ROOT/config.env"
  if [ -n "$AI_DEFAULT_PROVIDER" ]; then
    sudo mkdir -p "$STATE_ROOT"
    AI_SUPPORTED_PROVIDERS="$AI_PRIMARY_PROVIDERS_CSV" python3 - "$AI_DEFAULT_PROVIDER" <<'PY' | sudo tee "$STATE_ROOT/provider-selection.json" >/dev/null
import json
import os
import sys
provider = sys.argv[1]
supported = {item for item in os.environ["AI_SUPPORTED_PROVIDERS"].split(",") if item}
if provider not in supported:
    raise SystemExit(f"AI_DEFAULT_PROVIDER must be one of: {', '.join(sorted(supported))}")
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
    "auto_compact_enabled": False,
    "auto_compact_threshold_percent": 80,
}
try:
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
except json.JSONDecodeError:
    data = {}
data = default | data
data["auto_compact_enabled"] = False
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
Restart=always
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
Restart=always
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
if [ "$DRY_RUN" = false ] && { [ "$REQUEST_CLAUDE" = true ] || [ "$REQUEST_VSCODE_CLAUDE_BACKEND" = true ]; }; then
  CLAUDE_PREFLIGHT_LABEL="claude-code"
  if [ "$REQUEST_VSCODE_CLAUDE_BACKEND" = true ] && [ "$REQUEST_CLAUDE" != true ]; then
    CLAUDE_PREFLIGHT_LABEL="vscode Claude backend"
  fi
  root_has_command claude || { log "claude is required before core_ready for requested provider $CLAUDE_PREFLIGHT_LABEL"; exit 1; }
  root_env_run claude auth status --json >/dev/null || { log "claude auth/API config is required before core_ready for requested provider $CLAUDE_PREFLIGHT_LABEL"; exit 1; }
fi
if [ "$DRY_RUN" = false ] && [ "$REQUEST_CODEX" = true ]; then
  root_has_command codex || { log 'codex is required before core_ready for requested provider codex'; exit 1; }
  CODEX_EXEC_HELP_TEXT="$(root_env_run codex exec --help 2>&1)" || { log 'codex exec is required before core_ready for requested provider codex'; exit 1; }
  grep -q -- '--output-schema' <<< "$CODEX_EXEC_HELP_TEXT" || { log 'codex exec --output-schema is required for local strict config preflight'; exit 1; }
  CODEX_STRICT_CONFIG_PROBE_SCHEMA="$STATE_ROOT/.codex-strict-config-probe.$$.missing.json"
  rm -f "$CODEX_STRICT_CONFIG_PROBE_SCHEMA"
  CODEX_STRICT_CONFIG_ARGS=(exec --strict-config)
  grep -q -- '--ignore-rules' <<< "$CODEX_EXEC_HELP_TEXT" && CODEX_STRICT_CONFIG_ARGS+=(--ignore-rules)
  grep -q -- '--skip-git-repo-check' <<< "$CODEX_EXEC_HELP_TEXT" && CODEX_STRICT_CONFIG_ARGS+=(--skip-git-repo-check)
  CODEX_STRICT_CONFIG_ARGS+=(--cd "$WORKSPACE_ROOT" --output-schema "$CODEX_STRICT_CONFIG_PROBE_SCHEMA" --json 'strict config preflight')
  CODEX_STRICT_CONFIG_OUTPUT="$(root_env_run codex "${CODEX_STRICT_CONFIG_ARGS[@]}" 2>&1)" || {
    if grep -q 'Failed to read output schema file' <<< "$CODEX_STRICT_CONFIG_OUTPUT"; then
      :
    else
      printf '%s\n' "$CODEX_STRICT_CONFIG_OUTPUT" >&2
      log 'codex config.toml is not accepted by this Codex CLI under --strict-config'
      exit 1
    fi
  }
  if [ -n "$CODEX_STRICT_CONFIG_OUTPUT" ] && grep -q 'Error loading config.toml' <<< "$CODEX_STRICT_CONFIG_OUTPUT"; then
    printf '%s\n' "$CODEX_STRICT_CONFIG_OUTPUT" >&2
    log 'codex config.toml is not accepted by this Codex CLI under --strict-config'
    exit 1
  fi
  grep -q -- '--json' <<< "$CODEX_EXEC_HELP_TEXT" || { log 'codex exec --json is required for realtime Codex status events'; exit 1; }
  CODEX_EXEC_JSON_AVAILABLE=true
  CODEX_EXEC_RESUME_HELP_TEXT="$(root_env_run codex exec resume --help 2>&1)" || { log 'codex exec resume is required for default long conversation mode'; exit 1; }
  CODEX_EXEC_RESUME_AVAILABLE=true
  grep -q -- '--json' <<< "$CODEX_EXEC_RESUME_HELP_TEXT" || { log 'codex exec resume --json is required for default long conversation mode'; exit 1; }
  CODEX_EXEC_RESUME_JSON_AVAILABLE=true
  grep -q -- '--output-last-message' <<< "$CODEX_EXEC_RESUME_HELP_TEXT" || { log 'codex exec resume --output-last-message is required for default long conversation mode'; exit 1; }
  CODEX_EXEC_RESUME_OUTPUT_LAST_MESSAGE_AVAILABLE=true
  if grep -q -- '--ephemeral' <<< "$CODEX_EXEC_HELP_TEXT"; then
    CODEX_EXEC_EPHEMERAL_AVAILABLE=true
  fi
  grep -q -- '--cd' <<< "$CODEX_EXEC_HELP_TEXT" || { log 'codex exec --cd is required before core_ready for requested provider codex'; exit 1; }
  CODEX_EXEC_CD_AVAILABLE=true
  grep -q -- '--output-last-message' <<< "$CODEX_EXEC_HELP_TEXT" || { log 'codex exec --output-last-message is required before core_ready for requested provider codex'; exit 1; }
  CODEX_EXEC_OUTPUT_LAST_MESSAGE_AVAILABLE=true
  grep -q -- '--add-dir' <<< "$CODEX_EXEC_HELP_TEXT" || { log 'codex exec --add-dir is required for VM-wide full-access operation'; exit 1; }
  CODEX_EXEC_ADD_DIR_AVAILABLE=true
  grep -q -- '--skip-git-repo-check' <<< "$CODEX_EXEC_HELP_TEXT" || { log 'codex exec --skip-git-repo-check is required for arbitrary workspace operation'; exit 1; }
  CODEX_EXEC_SKIP_GIT_REPO_CHECK_AVAILABLE=true
  if grep -q -- '--dangerously-bypass-approvals-and-sandbox' <<< "$CODEX_EXEC_HELP_TEXT"; then
    CODEX_EXEC_FULL_ACCESS_MODE="bypass"
    CODEX_TELEGRAM_REALTIME_STATUS=true
    log 'codex full-access exec flag is available'
  elif grep -q -- '--sandbox' <<< "$CODEX_EXEC_HELP_TEXT"; then
    CODEX_EXEC_FULL_ACCESS_MODE="sandbox"
    CODEX_TELEGRAM_REALTIME_STATUS=true
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
  "requested_components": "$AI_RUNNER_COMPONENTS",
  "adapter_type": "$AI_ADAPTER_TYPE",
  "state_root": "$STATE_ROOT",
  "workspace_root": "$WORKSPACE_ROOT",
  "install_root": "$INSTALL_ROOT",
  "ai_tool_home": "$AI_TOOL_HOME",
  "codex_home": "$CODEX_HOME",
  "runner_enabled": true,
  "default_provider": "$AI_DEFAULT_PROVIDER",
  "configured_providers": "$AI_RUNNER_PROVIDERS",
  "systemd": $SYSTEMD,
  "wsl": $WSL,
  "core_ready": false,
  "core_ready_status": "pending_pairing",
  "claude_ready": $CLAUDE_READY,
  "codex_ready": $CODEX_READY,
  "codex_status": "$CODEX_STATUS",
  "codex_remediation_zh": "$CODEX_REMEDIATION_ZH",
  "codex_exec_json_available": $CODEX_EXEC_JSON_AVAILABLE,
  "codex_exec_ephemeral_available": $CODEX_EXEC_EPHEMERAL_AVAILABLE,
  "codex_exec_ephemeral_enabled": "$(if [ "$REQUEST_CODEX" = true ]; then printf '%s' "$EFFECTIVE_CODEX_EXEC_EPHEMERAL"; fi)",
  "codex_exec_resume_available": $CODEX_EXEC_RESUME_AVAILABLE,
  "codex_exec_resume_json_available": $CODEX_EXEC_RESUME_JSON_AVAILABLE,
  "codex_exec_resume_output_last_message_available": $CODEX_EXEC_RESUME_OUTPUT_LAST_MESSAGE_AVAILABLE,
  "codex_exec_cd_available": $CODEX_EXEC_CD_AVAILABLE,
  "codex_exec_output_last_message_available": $CODEX_EXEC_OUTPUT_LAST_MESSAGE_AVAILABLE,
  "codex_exec_add_dir_available": $CODEX_EXEC_ADD_DIR_AVAILABLE,
  "codex_exec_skip_git_repo_check_available": $CODEX_EXEC_SKIP_GIT_REPO_CHECK_AVAILABLE,
  "codex_exec_full_access_mode": "$CODEX_EXEC_FULL_ACCESS_MODE",
  "codex_telegram_realtime_status": $CODEX_TELEGRAM_REALTIME_STATUS,
  "vscode_ready": $VSCODE_READY,
  "vscode_root_wrapper": "$VSCODE_ROOT_WRAPPER",
  "vscode_root_dir": "$VSCODE_ROOT_DIR",
  "vscode_settings_dir": "$VSCODE_SETTINGS_DIR",
  "claude_model": "$CLAUDE_MODEL",
  "claude_max_turns": "$(if [ "$REQUEST_CLAUDE" = true ]; then printf '%s' "$EFFECTIVE_CLAUDE_MAX_TURNS"; fi)",
  "claude_api_retry_attempts": "$(if [ "$REQUEST_CLAUDE" = true ]; then printf '%s' "$EFFECTIVE_CLAUDE_API_RETRY_ATTEMPTS"; fi)",
  "claude_api_retry_sleep_seconds": "$(if [ "$REQUEST_CLAUDE" = true ]; then printf '%s' "$EFFECTIVE_CLAUDE_API_RETRY_SLEEP_SECONDS"; fi)",
  "vscode_claude_model": "$(if [ "$REQUEST_VSCODE" = true ]; then printf '%s' "$VSCODE_CLAUDE_MODEL"; fi)",
  "vscode_claude_max_turns": "$(if [ "$REQUEST_VSCODE" = true ]; then printf '%s' "$EFFECTIVE_VSCODE_CLAUDE_MAX_TURNS"; fi)",
  "vscode_claude_api_retry_attempts": "$(if [ "$REQUEST_VSCODE" = true ]; then printf '%s' "$EFFECTIVE_VSCODE_CLAUDE_API_RETRY_ATTEMPTS"; fi)",
  "vscode_claude_api_retry_sleep_seconds": "$(if [ "$REQUEST_VSCODE" = true ]; then printf '%s' "$EFFECTIVE_VSCODE_CLAUDE_API_RETRY_SLEEP_SECONDS"; fi)",
  "task_reserved_usd": "$EFFECTIVE_AI_TASK_RESERVED_USD",
  "permission_scope": "$AI_PERMISSION_SCOPE",
  "shell_confirmation_required": "$AI_REQUIRE_SHELL_CONFIRMATION",
  "process_control_enabled": "$EFFECTIVE_AI_PROCESS_CONTROL_ENABLED",
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
