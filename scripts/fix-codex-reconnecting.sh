#!/usr/bin/env bash
# 修复 Codex "Reconnecting... 5/5" 错误
# 这个脚本专门处理第三方 OpenAI 兼容代理在 Linux 服务器上的 websocket 连接问题

set -euo pipefail

STATE_ROOT="${AI_REMOTE_STATE:-/var/lib/ai-remote-runner}"
AI_TOOL_HOME="${AI_TOOL_HOME:-/root}"
CODEX_HOME="${AI_CODEX_HOME:-$AI_TOOL_HOME/.codex}"
CODEX_OPENAI_COMPAT_PROVIDER="ffc_openai_compat"

log() {
  printf '[fix-codex-reconnecting] %s\n' "$*"
}

check_codex_config() {
  if [ ! -f "$CODEX_HOME/config.toml" ]; then
    log "错误：$CODEX_HOME/config.toml 不存在"
    return 1
  fi

  log "当前 Codex 配置："
  grep -E "^(model_provider|openai_base_url|wire_api|supports_websockets|request_max_retries|stream_max_retries)" "$CODEX_HOME/config.toml" || true

  # 检查是否有自定义 provider 配置
  if grep -q "^\[model_providers\.$CODEX_OPENAI_COMPAT_PROVIDER\]" "$CODEX_HOME/config.toml"; then
    log "✓ 找到自定义 OpenAI 兼容 provider 配置"
    grep -A 10 "^\[model_providers\.$CODEX_OPENAI_COMPAT_PROVIDER\]" "$CODEX_HOME/config.toml" || true
    return 0
  else
    log "✗ 未找到自定义 provider 配置，需要修复"
    return 1
  fi
}

get_base_url() {
  local base_url=""

  # 从 config.env 读取
  if [ -f "$STATE_ROOT/config.env" ]; then
    base_url="$(grep '^CODEX_BASE_URL=' "$STATE_ROOT/config.env" | cut -d= -f2- || true)"
  fi

  # 从环境变量读取
  if [ -z "$base_url" ]; then
    base_url="${CODEX_BASE_URL:-}"
  fi

  # 从 Codex config.toml 读取
  if [ -z "$base_url" ] && [ -f "$CODEX_HOME/config.toml" ]; then
    base_url="$(grep '^openai_base_url' "$CODEX_HOME/config.toml" | sed 's/.*"\(.*\)".*/\1/' || true)"
  fi

  echo "$base_url"
}

apply_fix() {
  local base_url="$1"

  if [ -z "$base_url" ]; then
    log "错误：未提供 base_url"
    return 1
  fi

  if [ "$base_url" = "https://api.openai.com/v1" ]; then
    log "检测到官方 OpenAI API，不需要修复"
    return 0
  fi

  log "应用修复：配置第三方 OpenAI 兼容代理..."
  log "Base URL: $base_url"

  # 备份原配置
  if [ -f "$CODEX_HOME/config.toml" ]; then
    cp "$CODEX_HOME/config.toml" "$CODEX_HOME/config.toml.backup.$(date +%s)"
    log "已备份原配置到 $CODEX_HOME/config.toml.backup.*"
  fi

  # 读取当前配置
  local current_model current_review_model
  if [ -f "$CODEX_HOME/config.toml" ]; then
    current_model="$(grep '^model =' "$CODEX_HOME/config.toml" | sed 's/.*"\(.*\)".*/\1/' || echo 'gpt-5.5')"
    current_review_model="$(grep '^review_model =' "$CODEX_HOME/config.toml" | sed 's/.*"\(.*\)".*/\1/' || echo 'gpt-5.5')"
  else
    current_model="gpt-5.5"
    current_review_model="gpt-5.5"
  fi

  # 生成新配置
  sudo tee "$CODEX_HOME/config.toml" >/dev/null <<EOF
model_provider = "$CODEX_OPENAI_COMPAT_PROVIDER"
model = "$current_model"
review_model = "$current_review_model"
model_reasoning_effort = "xhigh"
approval_policy = "never"
sandbox_mode = "danger-full-access"
model_context_window = 200000
model_auto_compact_token_limit = 160000
hide_agent_reasoning = false

[shell_environment_policy]
inherit = "all"

[sandbox_workspace_write]
network_access = true

[features]
goals = true

[model_providers.$CODEX_OPENAI_COMPAT_PROVIDER]
name = "OpenAI-compatible proxy"
base_url = "$base_url"
wire_api = "responses"
env_key = "OPENAI_API_KEY"
supports_websockets = false
request_max_retries = 6
stream_max_retries = 10
stream_idle_timeout_ms = 600000
EOF

  sudo chmod 0600 "$CODEX_HOME/config.toml"
  sudo chown root:root "$CODEX_HOME/config.toml" 2>/dev/null || true

  log "✓ 配置已更新"
  log ""
  log "关键修复项："
  log "  - model_provider: $CODEX_OPENAI_COMPAT_PROVIDER"
  log "  - base_url: $base_url"
  log "  - wire_api: responses (避免 websocket)"
  log "  - supports_websockets: false"
  log "  - request_max_retries: 6"
  log "  - stream_max_retries: 10"
  log "  - stream_idle_timeout_ms: 600000 (10分钟)"
}

verify_fix() {
  log "验证修复..."

  if ! command -v codex >/dev/null 2>&1; then
    log "警告：codex 命令不可用"
    return 1
  fi

  # 简单测试：检查 codex 能否启动
  if codex --version >/dev/null 2>&1; then
    log "✓ Codex CLI 可以正常启动"
  else
    log "✗ Codex CLI 启动失败"
    return 1
  fi

  # 检查配置
  if check_codex_config; then
    log "✓ 配置验证通过"
    return 0
  else
    log "✗ 配置验证失败"
    return 1
  fi
}

# 主流程
main() {
  log "开始诊断和修复 Codex 'Reconnecting... 5/5' 错误..."
  log ""

  # 检查当前配置
  log "步骤 1: 检查当前配置"
  if check_codex_config; then
    log "当前配置看起来正确，但如果仍有问题，请继续修复"
    read -p "是否继续修复？(y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
      log "取消修复"
      exit 0
    fi
  fi

  log ""
  log "步骤 2: 获取 base_url"
  BASE_URL="$(get_base_url)"

  if [ -z "$BASE_URL" ]; then
    log "未检测到 CODEX_BASE_URL 配置"
    log "请提供第三方 OpenAI 兼容代理地址："
    read -p "Base URL: " BASE_URL

    if [ -z "$BASE_URL" ]; then
      log "错误：必须提供 base_url"
      exit 1
    fi
  else
    log "检测到 base_url: $BASE_URL"
  fi

  log ""
  log "步骤 3: 应用修复"
  apply_fix "$BASE_URL"

  log ""
  log "步骤 4: 验证修复"
  if verify_fix; then
    log ""
    log "========================================="
    log "修复完成！"
    log "========================================="
    log ""
    log "下一步："
    log "1. 重启 Telegram bot: sudo systemctl restart ai-telegram-bot"
    log "2. 在 Telegram 中测试: 发送简单消息给 bot"
    log "3. 如果仍有问题，检查 API key 和网络连接"
    log ""
    log "技术说明："
    log "本修复将 Codex 配置为使用 HTTP responses API 而非 websocket，"
    log "并增加了重试次数和超时时间，适配第三方 OpenAI 兼容代理"
  else
    log ""
    log "修复验证失败，请检查日志"
    exit 1
  fi
}

# 如果直接运行脚本
if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
  main "$@"
fi
