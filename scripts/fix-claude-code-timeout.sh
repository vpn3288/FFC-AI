#!/usr/bin/env bash
set -euo pipefail

CLAUDE_SETTINGS="${CLAUDE_SETTINGS:-$HOME/.claude/settings.json}"
STATE_ROOT="${AI_REMOTE_STATE:-/var/lib/ai-remote-runner}"
CONFIG_ENV="$STATE_ROOT/config.env"

log() {
  printf '[fix-claude-code-timeout] %s\n' "$*"
}

backup_settings() {
  if [ -f "$CLAUDE_SETTINGS" ]; then
    local backup="$CLAUDE_SETTINGS.backup.$(date +%Y%m%d_%H%M%S)"
    cp "$CLAUDE_SETTINGS" "$backup"
    log "已备份原配置到: $backup"
  fi
}

detect_third_party_api() {
  local base_url=""

  if [ -f "$CONFIG_ENV" ]; then
    base_url=$(grep '^ANTHROPIC_BASE_URL=' "$CONFIG_ENV" 2>/dev/null | cut -d= -f2- | tr -d '"' || true)
  fi

  if [ -f "$CLAUDE_SETTINGS" ] && [ -z "$base_url" ]; then
    base_url=$(python3 -c "import json; data=json.load(open('$CLAUDE_SETTINGS')); print(data.get('env', {}).get('ANTHROPIC_BASE_URL', ''))" 2>/dev/null || true)
  fi

  if [ -z "$base_url" ]; then
    echo ""
  elif echo "$base_url" | grep -qE '(anthropic\.com|claude\.ai)'; then
    echo ""
  else
    echo "$base_url"
  fi
}

apply_fix() {
  log "开始修复 Claude Code 配置..."

  local third_party_api
  third_party_api=$(detect_third_party_api)

  if [ -z "$third_party_api" ]; then
    log "检测到官方 Anthropic API，使用默认配置"
    log "如果您使用第三方 API，请先设置 ANTHROPIC_BASE_URL"
    return 0
  fi

  log "检测到第三方 API: $third_party_api"
  log "应用优化配置..."

  mkdir -p "$(dirname "$CLAUDE_SETTINGS")"
  backup_settings

  python3 <<'PYEOF'
import json
import os
from pathlib import Path

settings_path = Path(os.environ["CLAUDE_SETTINGS"])

try:
    data = json.loads(settings_path.read_text(encoding="utf-8")) if settings_path.exists() else {}
except json.JSONDecodeError:
    data = {}

env = data.get("env", {})
if not isinstance(env, dict):
    env = {}

if not env.get("ANTHROPIC_BASE_URL"):
    config_env = Path(os.environ.get("STATE_ROOT", "/var/lib/ai-remote-runner")) / "config.env"
    if config_env.exists():
        for line in config_env.read_text(encoding="utf-8").splitlines():
            if line.startswith("ANTHROPIC_BASE_URL="):
                base_url = line.split("=", 1)[1].strip().strip('"')
                if base_url:
                    env["ANTHROPIC_BASE_URL"] = base_url
                break

if env.get("ANTHROPIC_BASE_URL"):
    data["thirdPartyApi"] = True
    data["requestTimeout"] = int(os.environ.get("CLAUDE_REQUEST_TIMEOUT", "180000"))
    data["maxRetries"] = int(os.environ.get("CLAUDE_MAX_RETRIES", "5"))
    data["streamTimeout"] = int(os.environ.get("CLAUDE_STREAM_TIMEOUT", "600000"))

    if "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC" not in env:
        env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"

data["env"] = env

settings_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
settings_path.chmod(0o600)

print(f"✅ 配置已写入: {settings_path}")
print(f"   - requestTimeout: {data.get('requestTimeout', 'N/A')}")
print(f"   - maxRetries: {data.get('maxRetries', 'N/A')}")
print(f"   - streamTimeout: {data.get('streamTimeout', 'N/A')}")
print(f"   - thirdPartyApi: {data.get('thirdPartyApi', False)}")
PYEOF

  chmod 600 "$CLAUDE_SETTINGS"
  log "✅ 修复完成"
}

verify_fix() {
  log ""
  log "验证配置..."

  if [ ! -f "$CLAUDE_SETTINGS" ]; then
    log "❌ settings.json 不存在"
    return 1
  fi

  if grep -q '"thirdPartyApi"' "$CLAUDE_SETTINGS"; then
    log "✅ 第三方 API 优化配置已启用"
  else
    log "⚠️  未检测到第三方 API 优化配置"
  fi

  if grep -q '"requestTimeout"' "$CLAUDE_SETTINGS"; then
    log "✅ 请求超时配置已设置"
  else
    log "⚠️  未设置请求超时"
  fi

  log ""
  log "当前配置:"
  cat "$CLAUDE_SETTINGS" | sed 's/^/  /'
}

show_next_steps() {
  log ""
  log "后续步骤:"
  log "1. 重启服务: sudo systemctl restart ai-telegram-bot"
  log "2. 运行诊断: bash scripts/diagnose-claude-code.sh"
  log "3. 测试功能: 在 Telegram 发送 '/ai 状态'"
}

main() {
  log "Claude Code 超时和重试优化脚本"
  log "Settings 路径: $CLAUDE_SETTINGS"
  log "Config 路径: $CONFIG_ENV"
  log ""

  apply_fix
  verify_fix
  show_next_steps
}

main "$@"
