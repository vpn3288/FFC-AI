#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${FFC_AI_REPO_URL:-https://github.com/vpn3288/FFC-AI.git}"
REPO_DIR="${FFC_AI_REPO_DIR:-/root/FFC-AI}"
DEFAULT_COMPONENTS="${AI_RUNNER_COMPONENTS:-all,telegram}"
NONINTERACTIVE="${FFC_AI_NONINTERACTIVE:-false}"
SKIP_TELEGRAM_PAIR="${FFC_AI_SKIP_TELEGRAM_PAIR:-false}"
ALLOW_MISSING_API_KEYS="${FFC_AI_ALLOW_MISSING_API_KEYS:-false}"
DRY_RUN=false

usage() {
  printf 'usage: %s [--dry-run] [--repo-url URL] [--repo-dir PATH]\n' "$0"
  printf '       Default installs all primary tools plus Telegram: AI_RUNNER_COMPONENTS=all,telegram\n'
  printf '       Set FFC_AI_ALLOW_MISSING_API_KEYS=true only for already-authenticated advanced installs.\n'
}

log() {
  printf '[bootstrap-debian12] %s\n' "$*"
}

is_truthy() {
  case "${1:-}" in
    1|true|TRUE|yes|YES|on|ON) return 0 ;;
    *) return 1 ;;
  esac
}

is_interactive() {
  [ -t 0 ] && ! is_truthy "$NONINTERACTIVE"
}

prompt_default() {
  local var_name="$1"
  local prompt="$2"
  local default_value="$3"
  local current_value="${!var_name:-}"
  if [ -n "$current_value" ]; then
    return 0
  fi
  if is_interactive; then
    local answer=""
    if [ -n "$default_value" ]; then
      read -r -p "$prompt [$default_value]: " answer || answer=""
      printf -v "$var_name" '%s' "${answer:-$default_value}"
    else
      read -r -p "$prompt: " answer || answer=""
      printf -v "$var_name" '%s' "$answer"
    fi
  else
    printf -v "$var_name" '%s' "$default_value"
  fi
}

prompt_secret() {
  local var_name="$1"
  local prompt="$2"
  local current_value="${!var_name:-}"
  if [ -n "$current_value" ]; then
    return 0
  fi
  if is_interactive; then
    local answer=""
    read -r -s -p "$prompt: " answer || answer=""
    printf '\n'
    printf -v "$var_name" '%s' "$answer"
  fi
}

require_value() {
  local var_name="$1"
  local message="$2"
  local current_value="${!var_name:-}"
  if [ -n "$current_value" ] || [ "$DRY_RUN" = true ] || is_truthy "$ALLOW_MISSING_API_KEYS"; then
    return 0
  fi
  log "$message"
  log 'Set FFC_AI_ALLOW_MISSING_API_KEYS=true only if this root account is already authenticated and you accept configuring keys later.'
  exit 2
}

components_include() {
  local needle="$1"
  case ",$AI_RUNNER_COMPONENTS," in
    *,all,*|*,full,*|*,core,*)
      case "$needle" in
        codex|claude-code|vscode|telegram) return 0 ;;
      esac
      ;;
    *",$needle,"*) return 0 ;;
  esac
  if [ "$needle" = "claude-code" ] && [[ ",$AI_RUNNER_COMPONENTS," == *",claude,"* ]]; then
    return 0
  fi
  if [ "$needle" = "vscode" ] && [[ ",$AI_RUNNER_COMPONENTS," == *",code,"* ]]; then
    return 0
  fi
  return 1
}

normalize_components_choice() {
  local raw
  raw="$(printf '%s' "$1" | tr -d '[:space:]' | tr '[:upper:]' '[:lower:]')"
  case "$raw" in
    ''|1|all|full|core|all,telegram|full,telegram|core,telegram) printf 'all,telegram' ;;
    2|codex|codex,telegram) printf 'codex,telegram' ;;
    3|claude|claude-code|claude-code,telegram) printf 'claude-code,telegram' ;;
    4|vscode|code|vscode,telegram|code,telegram) printf 'vscode,telegram' ;;
    5|vscode-only|code-only) printf 'vscode' ;;
    *) printf '%s' "$1" ;;
  esac
}

validate_components() {
  local raw="$1"
  local component=""
  IFS=',' read -r -a parts <<< "$raw"
  for component in "${parts[@]}"; do
    component="$(printf '%s' "$component" | tr -d '[:space:]' | tr '[:upper:]' '[:lower:]')"
    case "$component" in
      all|full|core|codex|claude|claude-code|vscode|code|runner|telegram) ;;
      *) log "unsupported AI_RUNNER_COMPONENTS entry: $component"; exit 2 ;;
    esac
  done
}

install_host_dependencies() {
  log 'installing Debian host dependencies'
  if command -v apt-get >/dev/null 2>&1; then
    if [ "$DRY_RUN" = true ]; then
      log 'would run apt-get update and install sudo git curl ca-certificates'
    else
      apt-get update
      DEBIAN_FRONTEND=noninteractive apt-get install -y sudo git curl ca-certificates
    fi
  else
    log 'apt-get is unavailable; this bootstrap is intended for Debian 12'
    exit 1
  fi
}

sync_repo() {
  log "syncing repository into $REPO_DIR"
  if [ "$DRY_RUN" = true ]; then
    log "would clone or update $REPO_URL at $REPO_DIR"
    return 0
  fi
  if [ -d "$REPO_DIR/.git" ]; then
    git -C "$REPO_DIR" pull --ff-only
  else
    mkdir -p "$(dirname "$REPO_DIR")"
    git clone "$REPO_URL" "$REPO_DIR"
  fi
}

choose_components() {
  if [ -n "${AI_RUNNER_COMPONENTS:-}" ]; then
    AI_RUNNER_COMPONENTS="$(normalize_components_choice "$AI_RUNNER_COMPONENTS")"
  elif is_interactive; then
    printf '\n'
    printf '请选择安装模式:\n'
    printf '  1) all,telegram      安装 Claude Code + Codex + VSCode + Telegram\n'
    printf '  2) codex,telegram    只安装 Codex + Telegram\n'
    printf '  3) claude-code,telegram 只安装 Claude Code + Telegram\n'
    printf '  4) vscode,telegram   安装 VSCode/root wrapper + Telegram\n'
    printf '  5) vscode            只安装 VSCode/root wrapper\n'
    prompt_default AI_RUNNER_COMPONENTS '输入序号或组件名' "$DEFAULT_COMPONENTS"
    AI_RUNNER_COMPONENTS="$(normalize_components_choice "$AI_RUNNER_COMPONENTS")"
  else
    AI_RUNNER_COMPONENTS="$(normalize_components_choice "$DEFAULT_COMPONENTS")"
  fi
  validate_components "$AI_RUNNER_COMPONENTS"
  export AI_RUNNER_COMPONENTS
  log "selected AI_RUNNER_COMPONENTS=$AI_RUNNER_COMPONENTS"
}

collect_api_config() {
  if components_include codex; then
    log 'Codex/OpenAI-compatible config'
    prompt_default CODEX_BASE_URL 'Codex/OpenAI API base URL, empty means https://api.openai.com/v1' "${CODEX_BASE_URL:-}"
    prompt_default CODEX_MODEL 'Codex model' "${CODEX_MODEL:-gpt-5.5}"
    prompt_secret OPENAI_API_KEY 'OpenAI/Codex API key'
    require_value OPENAI_API_KEY 'OPENAI_API_KEY is required for beginner Codex installs.'
    export CODEX_MODEL
    [ -n "${CODEX_BASE_URL:-}" ] && export CODEX_BASE_URL
    [ -n "${OPENAI_API_KEY:-}" ] && export OPENAI_API_KEY
  fi
  if components_include claude-code || components_include vscode; then
    log 'Claude/Anthropic-compatible config for Claude Code or VSCode backend'
    prompt_default ANTHROPIC_BASE_URL 'Anthropic API base URL, empty means official default' "${ANTHROPIC_BASE_URL:-}"
    if components_include claude-code; then
      prompt_default CLAUDE_MODEL 'Claude Code model, empty means CLI default' "${CLAUDE_MODEL:-}"
    fi
    if components_include vscode; then
      prompt_default VSCODE_CLAUDE_MODEL 'VSCode Claude backend model' "${VSCODE_CLAUDE_MODEL:-gpt-5.5}"
      export VSCODE_CLAUDE_MODEL
    fi
    prompt_secret ANTHROPIC_AUTH_TOKEN 'Anthropic/Claude API key'
    require_value ANTHROPIC_AUTH_TOKEN 'ANTHROPIC_AUTH_TOKEN is required for beginner Claude Code/VSCode backend installs.'
    [ -n "${ANTHROPIC_BASE_URL:-}" ] && export ANTHROPIC_BASE_URL
    [ -n "${ANTHROPIC_AUTH_TOKEN:-}" ] && export ANTHROPIC_AUTH_TOKEN
    [ -n "${CLAUDE_MODEL:-}" ] && export CLAUDE_MODEL
  fi
  export AI_PERMISSION_SCOPE="${AI_PERMISSION_SCOPE:-full}"
  export AI_PROCESS_CONTROL_ENABLED="${AI_PROCESS_CONTROL_ENABLED:-1}"
}

collect_telegram_config() {
  if ! components_include telegram; then
    return 0
  fi
  log 'Telegram pairing config'
  prompt_secret TELEGRAM_BOT_TOKEN 'Telegram BotFather token, leave empty to pair later'
  if [ -n "${TELEGRAM_BOT_TOKEN:-}" ]; then
    prompt_default TELEGRAM_ALLOWED_CHAT_IDS 'Telegram chat_id, empty means discovery mode after install' "${TELEGRAM_ALLOWED_CHAT_IDS:-}"
  fi
}

run_installer() {
  log 'running install-runner.sh'
  if [ "$DRY_RUN" = true ]; then
    bash "$REPO_DIR/scripts/install-runner.sh" --dry-run
  else
    bash "$REPO_DIR/scripts/install-runner.sh"
  fi
}

pair_telegram_if_possible() {
  if ! components_include telegram || is_truthy "$SKIP_TELEGRAM_PAIR"; then
    return 0
  fi
  if [ -z "${TELEGRAM_BOT_TOKEN:-}" ]; then
    log 'Telegram token was not supplied; run scripts/pair-telegram.sh later'
    return 0
  fi
  log 'pairing Telegram without exposing the token as a process argument'
  if [ "$DRY_RUN" = true ]; then
    if [ -n "${TELEGRAM_ALLOWED_CHAT_IDS:-}" ]; then
      log "would run pair-telegram.sh --bot-token-stdin --chat-id $TELEGRAM_ALLOWED_CHAT_IDS"
    else
      log 'would run pair-telegram.sh --bot-token-stdin --discover-chat-id'
    fi
    return 0
  fi
  if [ -n "${TELEGRAM_ALLOWED_CHAT_IDS:-}" ]; then
    printf '%s' "$TELEGRAM_BOT_TOKEN" | bash "$REPO_DIR/scripts/pair-telegram.sh" --bot-token-stdin --chat-id "$TELEGRAM_ALLOWED_CHAT_IDS"
  else
    printf '%s' "$TELEGRAM_BOT_TOKEN" | bash "$REPO_DIR/scripts/pair-telegram.sh" --bot-token-stdin --discover-chat-id
  fi
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=true ;;
    --repo-url) REPO_URL="$2"; shift ;;
    --repo-dir) REPO_DIR="$2"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage; exit 2 ;;
  esac
  shift
done

if [ "$(id -u)" != "0" ]; then
  log 'please run this bootstrap as root, for example: curl -fsSL ... | sudo bash'
  exit 1
fi

install_host_dependencies
sync_repo
choose_components
collect_api_config
collect_telegram_config
run_installer
pair_telegram_if_possible

log 'done. Open Telegram and send /ai 状态 after pairing completes.'
