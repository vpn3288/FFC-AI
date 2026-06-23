#!/usr/bin/env bash
# 配置验证脚本 - 验证第三方API配置是否正确持久化
set -euo pipefail

STATE_ROOT="${AI_REMOTE_STATE:-/var/lib/ai-remote-runner}"
CODEX_HOME="${CODEX_HOME:-${AI_CODEX_HOME:-/root/.codex}}"
CLAUDE_SETTINGS="/root/.claude/settings.json"
VSCODE_SETTINGS="/root/.vscode-root/User/settings.json"

log() {
  printf '[validate-config] %s\n' "$*"
}

error() {
  printf '[validate-config] ERROR: %s\n' "$*" >&2
}

check_file_exists() {
  local file="$1"
  local desc="$2"
  if [ -f "$file" ]; then
    log "✓ $desc 存在: $file"
    return 0
  else
    error "✗ $desc 不存在: $file"
    return 1
  fi
}

check_config_value() {
  local file="$1"
  local key="$2"
  local desc="$3"
  if [ ! -f "$file" ]; then
    error "✗ 配置文件不存在: $file"
    return 1
  fi

  if grep -q "^${key}=" "$file" 2>/dev/null; then
    local value
    value=$(grep "^${key}=" "$file" | cut -d= -f2-)
    if [ -n "$value" ]; then
      log "✓ $desc 已配置: $key"
      return 0
    else
      error "✗ $desc 为空: $key"
      return 1
    fi
  else
    log "○ $desc 未配置: $key (可能使用默认值)"
    return 0
  fi
}

check_toml_value() {
  local file="$1"
  local key="$2"
  local desc="$3"
  if [ ! -f "$file" ]; then
    error "✗ 配置文件不存在: $file"
    return 1
  fi

  if grep -q "^${key}\s*=" "$file" 2>/dev/null; then
    local value
    value=$(grep "^${key}\s*=" "$file" | cut -d= -f2- | tr -d ' "')
    if [ -n "$value" ]; then
      log "✓ $desc 已配置: $key=$value"
      return 0
    else
      error "✗ $desc 为空: $key"
      return 1
    fi
  else
    log "○ $desc 未配置: $key"
    return 0
  fi
}

check_codex_base_url() {
  local file="$1"
  if [ ! -f "$file" ]; then
    error "✗ Codex配置文件不存在: $file"
    return 1
  fi
  if grep -q '^openai_base_url\s*=' "$file" 2>/dev/null; then
    log "✓ Codex Base URL 已配置: openai_base_url"
    return 0
  fi
  if grep -q '^\[model_providers\.' "$file" 2>/dev/null && grep -q '^base_url\s*=' "$file" 2>/dev/null; then
    log "✓ Codex Base URL 已配置: model_providers.*.base_url"
    return 0
  fi
  log "○ Codex Base URL 未配置（可能使用官方默认值）"
  return 0
}

check_json_key() {
  local file="$1"
  local key="$2"
  local desc="$3"
  if [ ! -f "$file" ]; then
    error "✗ 配置文件不存在: $file"
    return 1
  fi

  if command -v python3 >/dev/null 2>&1; then
    if python3 -c "import json; data=json.load(open('$file')); exit(0 if '$key' in str(data) else 1)" 2>/dev/null; then
      log "✓ $desc 已配置: $key"
      return 0
    else
      log "○ $desc 未配置: $key"
      return 0
    fi
  else
    if grep -q "\"$key\"" "$file" 2>/dev/null; then
      log "✓ $desc 包含: $key"
      return 0
    else
      log "○ $desc 未包含: $key"
      return 0
    fi
  fi
}

log "====== 开始配置验证 ======"

ERRORS=0

# 验证基础配置文件
log ""
log "=== 1. 基础配置文件 ==="
check_file_exists "$STATE_ROOT/config.env" "Runner配置文件" || ((ERRORS++))
check_file_exists "$STATE_ROOT/install-manifest.json" "安装清单" || ((ERRORS++))

# 验证Codex配置
log ""
log "=== 2. Codex配置 ==="
if [ -d "$CODEX_HOME" ]; then
  log "✓ Codex配置目录存在: $CODEX_HOME"

  if [ -f "$CODEX_HOME/config.toml" ]; then
    log "✓ Codex配置文件存在"
    check_toml_value "$CODEX_HOME/config.toml" "model" "Codex模型"
    check_codex_base_url "$CODEX_HOME/config.toml"
    check_toml_value "$CODEX_HOME/config.toml" "approval_policy" "Codex审批策略"
    check_toml_value "$CODEX_HOME/config.toml" "sandbox_mode" "Codex沙箱模式"
  else
    log "○ Codex配置文件不存在（可能未安装Codex）"
  fi

  if [ -f "$CODEX_HOME/auth.json" ]; then
    log "✓ Codex认证文件存在"
    check_json_key "$CODEX_HOME/auth.json" "OPENAI_API_KEY" "Codex API Key"
  else
    log "○ Codex认证文件不存在"
  fi
else
  log "○ Codex未安装或配置目录不存在"
fi

# 验证Claude Code配置
log ""
log "=== 3. Claude Code配置 ==="
if [ -f "$CLAUDE_SETTINGS" ]; then
  log "✓ Claude设置文件存在"
  check_json_key "$CLAUDE_SETTINGS" "ANTHROPIC_BASE_URL" "Anthropic Base URL"
  check_json_key "$CLAUDE_SETTINGS" "ANTHROPIC_AUTH_TOKEN" "Anthropic Auth Token"
  check_json_key "$CLAUDE_SETTINGS" "CLAUDE_MODEL" "Claude模型"
else
  log "○ Claude设置文件不存在（可能未安装Claude Code）"
fi

# 验证VSCode配置
log ""
log "=== 4. VSCode配置 ==="
if [ -f "$VSCODE_SETTINGS" ]; then
  log "✓ VSCode设置文件存在"
  check_json_key "$VSCODE_SETTINGS" "security.workspace.trust.enabled" "VSCode工作区信任"
  check_json_key "$VSCODE_SETTINGS" "telemetry.telemetryLevel" "VSCode遥测设置"
else
  log "○ VSCode设置文件不存在（可能未安装VSCode）"
fi

# 验证config.env中的第三方API配置
log ""
log "=== 5. config.env第三方API配置 ==="
if [ -f "$STATE_ROOT/config.env" ]; then
  check_config_value "$STATE_ROOT/config.env" "CODEX_BASE_URL" "Codex第三方API地址"
  check_config_value "$STATE_ROOT/config.env" "ANTHROPIC_BASE_URL" "Anthropic第三方API地址"
  check_config_value "$STATE_ROOT/config.env" "OPENAI_API_KEY" "OpenAI API Key"
  check_config_value "$STATE_ROOT/config.env" "ANTHROPIC_AUTH_TOKEN" "Anthropic Auth Token"
else
  error "✗ config.env文件不存在"
  ((ERRORS++))
fi

# 总结
log ""
log "====== 验证完成 ======"
if [ $ERRORS -eq 0 ]; then
  log "✓ 所有必需配置验证通过"
  exit 0
else
  error "✗ 发现 $ERRORS 个错误"
  exit 1
fi
