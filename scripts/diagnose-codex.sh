#!/usr/bin/env bash
# Codex 连接问题诊断工具
# 用于诊断 "Reconnecting... 5/5" 和其他 Codex 相关问题

set -euo pipefail

STATE_ROOT="${AI_REMOTE_STATE:-/var/lib/ai-remote-runner}"
AI_TOOL_HOME="${AI_TOOL_HOME:-/root}"
CODEX_HOME="${AI_CODEX_HOME:-$AI_TOOL_HOME/.codex}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() {
  echo -e "${GREEN}[✓]${NC} $*"
}

log_warn() {
  echo -e "${YELLOW}[!]${NC} $*"
}

log_error() {
  echo -e "${RED}[✗]${NC} $*"
}

check_command() {
  local cmd="$1"
  if command -v "$cmd" >/dev/null 2>&1; then
    log_info "$cmd 已安装"
    "$cmd" --version 2>&1 | head -1 || true
    return 0
  else
    log_error "$cmd 未安装"
    return 1
  fi
}

check_file() {
  local file="$1"
  if [ -f "$file" ]; then
    log_info "文件存在: $file"
    return 0
  else
    log_error "文件不存在: $file"
    return 1
  fi
}

check_config_value() {
  local key="$1"
  local file="$2"
  if [ ! -f "$file" ]; then
    log_warn "$file 不存在"
    return 1
  fi

  local value
  value="$(grep "^$key=" "$file" 2>/dev/null | cut -d= -f2- || true)"

  if [ -n "$value" ]; then
    # 隐藏敏感信息
    case "$key" in
      *API_KEY*|*AUTH_TOKEN*|*SECRET*)
        log_info "$key = ${value:0:8}..."
        ;;
      *)
        log_info "$key = $value"
        ;;
    esac
    return 0
  else
    log_warn "$key 未设置"
    return 1
  fi
}

check_toml_value() {
  local key="$1"
  local file="$2"
  if [ ! -f "$file" ]; then
    log_warn "$file 不存在"
    return 1
  fi

  local value
  value="$(grep "^$key" "$file" 2>/dev/null | sed 's/.*=\s*"\?\([^"]*\)"\?.*/\1/' || true)"

  if [ -n "$value" ]; then
    log_info "$key = $value"
    return 0
  else
    log_warn "$key 未设置"
    return 1
  fi
}

check_network() {
  local url="$1"
  echo ""
  echo "===== 网络连接测试 ====="

  if curl -I --max-time 10 "$url" >/dev/null 2>&1; then
    log_info "可以连接到 $url"
    return 0
  else
    log_error "无法连接到 $url"
    log_warn "请检查网络连接和防火墙设置"
    return 1
  fi
}

main() {
  echo "======================================"
  echo "Codex 连接问题诊断工具"
  echo "======================================"
  echo ""

  echo "===== 系统环境 ====="
  echo "操作系统: $(uname -s) $(uname -r)"
  echo "当前用户: $(whoami)"
  echo "HOME: $HOME"
  echo "CODEX_HOME: $CODEX_HOME"
  echo ""

  echo "===== 必需命令检查 ====="
  check_command node || true
  check_command npm || true
  check_command codex || true
  check_command python3 || true
  echo ""

  echo "===== Codex 配置文件检查 ====="
  check_file "$CODEX_HOME/config.toml" || true
  check_file "$CODEX_HOME/auth.json" || true
  echo ""

  if [ -f "$CODEX_HOME/config.toml" ]; then
    echo "===== Codex TOML 配置详情 ====="
    check_toml_value "model_provider" "$CODEX_HOME/config.toml" || true
    check_toml_value "model" "$CODEX_HOME/config.toml" || true
    check_toml_value "openai_base_url" "$CODEX_HOME/config.toml" || true

    # 检查自定义 provider 配置
    if grep -q "^\[model_providers\.ffc_openai_compat\]" "$CODEX_HOME/config.toml" 2>/dev/null; then
      echo ""
      log_info "发现自定义 OpenAI 兼容 provider 配置："
      echo ""
      grep -A 10 "^\[model_providers\.ffc_openai_compat\]" "$CODEX_HOME/config.toml" | while IFS= read -r line; do
        echo "  $line"
      done
      echo ""

      # 关键配置检查
      if grep -q "wire_api.*responses" "$CODEX_HOME/config.toml" 2>/dev/null; then
        log_info "wire_api 已设置为 responses（正确）"
      else
        log_warn "wire_api 可能未正确设置"
      fi

      if grep -q "supports_websockets.*false" "$CODEX_HOME/config.toml" 2>/dev/null; then
        log_info "supports_websockets 已设置为 false（正确）"
      else
        log_warn "supports_websockets 可能未正确设置"
      fi
    else
      log_warn "未找到自定义 provider 配置"
      log_warn "这可能导致第三方代理出现 'Reconnecting... 5/5' 错误"
      echo ""
      log_warn "建议运行修复脚本: sudo bash scripts/fix-codex-reconnecting.sh"
    fi
    echo ""
  fi

  if [ -f "$STATE_ROOT/config.env" ]; then
    echo "===== Runner 环境配置检查 ====="
    check_config_value "OPENAI_API_KEY" "$STATE_ROOT/config.env" || true
    check_config_value "CODEX_BASE_URL" "$STATE_ROOT/config.env" || true
    check_config_value "CODEX_MODEL" "$STATE_ROOT/config.env" || true
    check_config_value "CODEX_MODEL_PROVIDER" "$STATE_ROOT/config.env" || true
    echo ""
  fi

  # 网络测试
  if [ -f "$STATE_ROOT/config.env" ]; then
    local base_url
    base_url="$(grep "^CODEX_BASE_URL=" "$STATE_ROOT/config.env" 2>/dev/null | cut -d= -f2- || true)"

    if [ -n "$base_url" ]; then
      check_network "$base_url" || true
    fi
  fi

  echo ""
  echo "===== 服务状态检查 ====="
  if systemctl is-active --quiet ai-telegram-bot 2>/dev/null; then
    log_info "ai-telegram-bot 服务正在运行"
  else
    log_warn "ai-telegram-bot 服务未运行"
  fi

  if systemctl is-active --quiet ai-remote-runner 2>/dev/null; then
    log_info "ai-remote-runner 服务正在运行"
  else
    log_warn "ai-remote-runner 服务未运行"
  fi
  echo ""

  echo "===== 最近错误日志（最后20行）====="
  if command -v journalctl >/dev/null 2>&1; then
    journalctl -u ai-telegram-bot -n 20 --no-pager 2>/dev/null | grep -i "reconnecting\|error\|failed" || log_info "未发现明显错误"
  else
    log_warn "journalctl 不可用，无法检查日志"
  fi
  echo ""

  echo "======================================"
  echo "诊断完成"
  echo "======================================"
  echo ""
  echo "如果发现配置问题，建议操作："
  echo "1. 运行修复脚本: sudo bash scripts/fix-codex-reconnecting.sh"
  echo "2. 重启服务: sudo systemctl restart ai-telegram-bot"
  echo "3. 查看详细日志: sudo journalctl -u ai-telegram-bot -f"
  echo ""
}

main "$@"
