#!/usr/bin/env bash
# 验证配置并安全重启服务

set -euo pipefail

STATE_ROOT="${AI_REMOTE_STATE:-/var/lib/ai-remote-runner}"
CONFIG_ENV="$STATE_ROOT/config.env"

log() {
  printf '[verify-restart] %s\n' "$*" >&2
}

echo "================================================================"
echo "          Claude Code 配置验证与服务重启工具"
echo "================================================================"
echo ""

# 1. 检查配置文件
log "1️⃣ 检查配置文件: $CONFIG_ENV"
if [ ! -f "$CONFIG_ENV" ]; then
  log "❌ 配置文件不存在！"
  exit 1
fi

echo "关键配置项："
echo "─────────────────────────────────────────────────────────────"
grep -E "^(CLAUDE_MAX_TURNS|AI_TASK_TIMEOUT_SECONDS|AI_LOCAL_EXEC_TIMEOUT_SECONDS|CLAUDE_API_RETRY_ATTEMPTS)=" "$CONFIG_ENV" | while read line; do
  echo "  ✓ $line"
done
echo ""

# 2. 检查运行中的服务
log "2️⃣ 检查运行中的服务环境变量"
if ! pgrep -f "ai_remote_runner.cli telegram" > /dev/null; then
  log "⚠️  服务未运行"
else
  echo "当前运行环境变量："
  echo "─────────────────────────────────────────────────────────────"
  ps eww $(pgrep -f "ai_remote_runner.cli telegram" | head -1) 2>/dev/null | tr ' ' '\n' | grep -E "^(CLAUDE_MAX_TURNS|AI_TASK_TIMEOUT_SECONDS|AI_LOCAL_EXEC_TIMEOUT_SECONDS|CLAUDE_API_RETRY_ATTEMPTS)=" | while read line; do
    echo "  • $line"
  done
  echo ""
fi

# 3. 对比差异
log "3️⃣ 检测配置差异"
CONFIG_MAX_TURNS=$(grep "^CLAUDE_MAX_TURNS=" "$CONFIG_ENV" 2>/dev/null | cut -d= -f2 || echo "未设置")
RUNNING_MAX_TURNS=$(ps eww $(pgrep -f "ai_remote_runner.cli telegram" | head -1) 2>/dev/null | tr ' ' '\n' | grep "^CLAUDE_MAX_TURNS=" | cut -d= -f2 || echo "未运行")

if [ "$CONFIG_MAX_TURNS" != "$RUNNING_MAX_TURNS" ]; then
  echo "  ⚠️  检测到配置不一致："
  echo "     配置文件: CLAUDE_MAX_TURNS=$CONFIG_MAX_TURNS"
  echo "     运行环境: CLAUDE_MAX_TURNS=$RUNNING_MAX_TURNS"
  echo "     需要重启服务使配置生效！"
  NEED_RESTART=true
else
  echo "  ✓ 配置已同步"
  NEED_RESTART=false
fi
echo ""

# 4. 询问是否重启
if [ "${NEED_RESTART:-false}" = "true" ]; then
  log "4️⃣ 准备重启服务"
  echo "即将执行："
  echo "  sudo systemctl restart ai-telegram-bot.service"
  echo ""
  read -p "确认重启服务？(y/N): " -r
  if [[ $REPLY =~ ^[Yy]$ ]]; then
    log "正在重启服务..."
    sudo systemctl restart ai-telegram-bot.service
    sleep 3
    
    log "验证服务状态..."
    if systemctl is-active --quiet ai-telegram-bot.service; then
      echo "✅ 服务重启成功！"
      echo ""
      echo "新环境变量："
      echo "─────────────────────────────────────────────────────────────"
      sleep 2
      ps eww $(pgrep -f "ai_remote_runner.cli telegram" | head -1) 2>/dev/null | tr ' ' '\n' | grep -E "^(CLAUDE_MAX_TURNS|AI_TASK_TIMEOUT_SECONDS|AI_LOCAL_EXEC_TIMEOUT_SECONDS|CLAUDE_API_RETRY_ATTEMPTS)=" | while read line; do
        echo "  ✓ $line"
      done
      echo ""
      echo "🎉 配置已生效！现在可以通过 Telegram 测试稳定性。"
    else
      echo "❌ 服务启动失败，请检查日志："
      echo "   journalctl -u ai-telegram-bot.service -n 50"
      exit 1
    fi
  else
    log "取消重启。配置将在下次服务重启时生效。"
    exit 0
  fi
else
  log "✅ 配置已是最新，无需重启。"
fi

echo ""
echo "================================================================"
