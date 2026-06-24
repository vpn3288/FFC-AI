#!/usr/bin/env bash
set -euo pipefail

CLAUDE_SETTINGS="${CLAUDE_SETTINGS:-$HOME/.claude/settings.json}"
STATE_ROOT="${AI_REMOTE_STATE:-/var/lib/ai-remote-runner}"
CONFIG_ENV="$STATE_ROOT/config.env"

log() {
  printf '[diagnose-claude-code] %s\n' "$*"
}

section() {
  printf '\n=== %s ===\n' "$1"
}

check_claude_installed() {
  section "检查 Claude Code CLI 安装状态"
  if ! command -v claude >/dev/null 2>&1; then
    log "❌ Claude Code CLI 未安装"
    log "修复：运行 'npm install -g @anthropic-ai/claude-code' 或 'curl https://install.claude.ai/cli | sh'"
    return 1
  fi
  local version
  version=$(claude --version 2>&1 | head -1)
  log "✅ Claude Code CLI 已安装: $version"
}

check_claude_auth() {
  section "检查 Claude Code 认证状态"
  if ! claude auth status --json >/dev/null 2>&1; then
    log "❌ Claude Code 认证失败"
    log "修复：运行 'claude auth login' 或配置 ANTHROPIC_AUTH_TOKEN 环境变量"
    return 1
  fi
  local auth_json
  auth_json=$(claude auth status --json 2>&1)
  log "✅ Claude Code 认证成功"
  log "认证详情: $auth_json"
}

check_claude_settings() {
  section "检查 Claude Code settings.json 配置"
  if [ ! -f "$CLAUDE_SETTINGS" ]; then
    log "❌ settings.json 不存在: $CLAUDE_SETTINGS"
    log "修复：运行 'bash scripts/fix-claude-code-timeout.sh' 创建优化配置"
    return 1
  fi
  log "✅ settings.json 存在: $CLAUDE_SETTINGS"
  log "内容:"
  cat "$CLAUDE_SETTINGS" | sed 's/^/  /'

  if grep -q '"thirdPartyApi"' "$CLAUDE_SETTINGS"; then
    log "✅ 检测到第三方 API 优化配置"
  else
    log "⚠️  未检测到第三方 API 优化配置"
    if grep -q '"ANTHROPIC_BASE_URL"' "$CLAUDE_SETTINGS"; then
      log "修复：运行 'bash scripts/fix-claude-code-timeout.sh' 添加优化配置"
    fi
  fi
}

check_claude_capabilities() {
  section "检查 Claude Code CLI 功能支持"
  local has_request_timeout=false
  local has_stream_timeout=false
  local has_max_retries=false
  local has_bare=false

  if claude -p --help 2>&1 | grep -q -- '--request-timeout'; then
    log "✅ 支持 --request-timeout"
    has_request_timeout=true
  else
    log "⚠️  不支持 --request-timeout (需要 Claude Code 2.1+)"
  fi

  if claude -p --help 2>&1 | grep -q -- '--stream-timeout'; then
    log "✅ 支持 --stream-timeout"
    has_stream_timeout=true
  else
    log "⚠️  不支持 --stream-timeout (需要 Claude Code 2.1+)"
  fi

  if claude -p --help 2>&1 | grep -q -- '--max-retries'; then
    log "✅ 支持 --max-retries"
    has_max_retries=true
  else
    log "⚠️  不支持 --max-retries (需要 Claude Code 2.1+)"
  fi

  if claude -p --help 2>&1 | grep -q -- '--bare'; then
    log "✅ 支持 --bare"
    has_bare=true
  else
    log "⚠️  不支持 --bare (需要 Claude Code 2.1+)"
  fi

  if [ "$has_request_timeout" = false ] || [ "$has_stream_timeout" = false ]; then
    log ""
    log "建议：升级到 Claude Code 2.1+ 以获得更好的第三方 API 支持"
    log "升级命令: npm update -g @anthropic-ai/claude-code"
  fi
}

check_third_party_api() {
  section "检查第三方 API 配置"
  local base_url=""

  if [ -f "$CONFIG_ENV" ]; then
    base_url=$(grep '^ANTHROPIC_BASE_URL=' "$CONFIG_ENV" 2>/dev/null | cut -d= -f2- | tr -d '"' || true)
  fi

  if [ -f "$CLAUDE_SETTINGS" ] && [ -z "$base_url" ]; then
    base_url=$(python3 -c "import json; data=json.load(open('$CLAUDE_SETTINGS')); print(data.get('env', {}).get('ANTHROPIC_BASE_URL', ''))" 2>/dev/null || true)
  fi

  if [ -z "$base_url" ]; then
    log "✅ 使用官方 Anthropic API"
    return 0
  fi

  log "✅ 检测到第三方 API: $base_url"

  if echo "$base_url" | grep -qE '(anthropic\.com|claude\.ai)'; then
    log "⚠️  看起来是官方 API，但设置了自定义 base URL"
  else
    log "第三方 API 需要以下优化配置:"
    log "  - requestTimeout: 180000 (3分钟)"
    log "  - streamTimeout: 600000 (10分钟)"
    log "  - maxRetries: 5"
    log "  - CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC: 1"

    if [ -f "$CLAUDE_SETTINGS" ] && grep -q '"requestTimeout"' "$CLAUDE_SETTINGS"; then
      log "✅ 已配置优化参数"
    else
      log "❌ 缺少优化参数"
      log "修复：运行 'bash scripts/fix-claude-code-timeout.sh'"
    fi
  fi
}

check_runner_config() {
  section "检查 AI Runner 配置"
  if [ ! -f "$CONFIG_ENV" ]; then
    log "⚠️  config.env 不存在: $CONFIG_ENV"
    return 0
  fi

  local claude_model
  claude_model=$(grep '^CLAUDE_MODEL=' "$CONFIG_ENV" 2>/dev/null | cut -d= -f2- | tr -d '"' || true)

  if [ -n "$claude_model" ]; then
    log "✅ CLAUDE_MODEL: $claude_model"
  else
    log "⚠️  CLAUDE_MODEL 未设置"
  fi

  local providers
  providers=$(grep '^AI_RUNNER_PROVIDERS=' "$CONFIG_ENV" 2>/dev/null | cut -d= -f2- | tr -d '"' || true)

  if echo "$providers" | grep -q 'claude-code'; then
    log "✅ claude-code 已启用"
  else
    log "⚠️  claude-code 未在 AI_RUNNER_PROVIDERS 中启用"
  fi
}

test_claude_basic() {
  section "测试 Claude Code 基本功能"
  log "执行简单测试: echo 'hello from claude'"

  local test_output
  if test_output=$(timeout 30 claude -p --bare --output-format json --permission-mode plan --tools '' --no-session-persistence 'reply with: OK' 2>&1); then
    log "✅ Claude Code 基本功能正常"
    if echo "$test_output" | grep -q '"result"'; then
      log "响应格式正确 (JSON)"
    fi
  else
    log "❌ Claude Code 测试失败"
    log "错误输出:"
    echo "$test_output" | sed 's/^/  /'
    return 1
  fi
}

main() {
  log "开始诊断 Claude Code 配置..."
  log "Claude settings: $CLAUDE_SETTINGS"
  log "Config env: $CONFIG_ENV"

  local has_issues=false

  check_claude_installed || has_issues=true
  check_claude_auth || has_issues=true
  check_claude_settings || has_issues=true
  check_claude_capabilities || true
  check_third_party_api || has_issues=true
  check_runner_config || true
  test_claude_basic || has_issues=true

  section "诊断总结"
  if [ "$has_issues" = true ]; then
    log "❌ 发现问题，请按照上述修复建议操作"
    log ""
    log "快速修复命令:"
    log "  bash scripts/fix-claude-code-timeout.sh"
    exit 1
  else
    log "✅ Claude Code 配置正常"
    exit 0
  fi
}

main "$@"
