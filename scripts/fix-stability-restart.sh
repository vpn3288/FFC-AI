#!/usr/bin/env bash
# 自动修复 Claude Code 稳定性问题并重启服务

set -euo pipefail

STATE_ROOT="${AI_REMOTE_STATE:-/var/lib/ai-remote-runner}"
CONFIG_ENV="$STATE_ROOT/config.env"

log() {
  printf '\033[1;36m[fix-stability]\033[0m %s\n' "$*"
}

success() {
  printf '\033[1;32m✅ %s\033[0m\n' "$*"
}

error() {
  printf '\033[1;31m❌ %s\033[0m\n' "$*"
}

warning() {
  printf '\033[1;33m⚠️  %s\033[0m\n' "$*"
}

echo ""
echo "╔════════════════════════════════════════════════════════════╗"
echo "║     Claude Code 稳定性自动修复工具                        ║"
echo "╚════════════════════════════════════════════════════════════╝"
echo ""

# 1. 验证配置文件
log "检查配置文件..."
if [ ! -f "$CONFIG_ENV" ]; then
  error "配置文件不存在: $CONFIG_ENV"
  exit 1
fi

# 2. 检查关键配置项
log "验证关键配置..."
CLAUDE_MAX_TURNS=$(grep "^CLAUDE_MAX_TURNS=" "$CONFIG_ENV" | cut -d= -f2)
AI_TASK_TIMEOUT=$(grep "^AI_TASK_TIMEOUT_SECONDS=" "$CONFIG_ENV" | cut -d= -f2)
RETRY_ATTEMPTS=$(grep "^CLAUDE_API_RETRY_ATTEMPTS=" "$CONFIG_ENV" | cut -d= -f2)

if [ "$CLAUDE_MAX_TURNS" = "50" ] && [ "$AI_TASK_TIMEOUT" = "3600" ] && [ "$RETRY_ATTEMPTS" = "8" ]; then
  success "配置文件已优化"
  echo "  CLAUDE_MAX_TURNS=50 ✓"
  echo "  AI_TASK_TIMEOUT_SECONDS=3600 ✓"
  echo "  CLAUDE_API_RETRY_ATTEMPTS=8 ✓"
else
  warning "配置文件未完全优化，当前值："
  echo "  CLAUDE_MAX_TURNS=$CLAUDE_MAX_TURNS"
  echo "  AI_TASK_TIMEOUT_SECONDS=$AI_TASK_TIMEOUT"
  echo "  CLAUDE_API_RETRY_ATTEMPTS=$RETRY_ATTEMPTS"
fi

# 3. 检查服务状态
log "检查服务运行状态..."
if ! systemctl is-active --quiet ai-telegram-bot.service; then
  warning "服务未运行，将尝试启动..."
  sudo systemctl start ai-telegram-bot.service
  sleep 3
fi

if systemctl is-active --quiet ai-telegram-bot.service; then
  success "服务正在运行"
else
  error "服务启动失败"
  exit 1
fi

# 4. 检查环境变量差异
log "检测配置差异..."
RUNNING_MAX_TURNS=$(ps eww $(pgrep -f "ai_remote_runner.cli telegram" | head -1) 2>/dev/null | tr ' ' '\n' | grep "^CLAUDE_MAX_TURNS=" | cut -d= -f2 || echo "0")

if [ "$CLAUDE_MAX_TURNS" != "$RUNNING_MAX_TURNS" ]; then
  warning "检测到配置不一致"
  echo "  配置文件: CLAUDE_MAX_TURNS=$CLAUDE_MAX_TURNS"
  echo "  运行进程: CLAUDE_MAX_TURNS=$RUNNING_MAX_TURNS"
  echo ""
  log "需要重启服务使配置生效"
  
  # 5. 重启服务
  log "正在重启 ai-telegram-bot.service..."
  sudo systemctl restart ai-telegram-bot.service
  
  log "等待服务启动..."
  sleep 5
  
  # 6. 验证重启
  if systemctl is-active --quiet ai-telegram-bot.service; then
    success "服务重启成功"
    
    # 验证新环境变量
    log "验证新环境变量..."
    sleep 2
    NEW_MAX_TURNS=$(ps eww $(pgrep -f "ai_remote_runner.cli telegram" | head -1) 2>/dev/null | tr ' ' '\n' | grep "^CLAUDE_MAX_TURNS=" | cut -d= -f2 || echo "未检测到")
    NEW_TIMEOUT=$(ps eww $(pgrep -f "ai_remote_runner.cli telegram" | head -1) 2>/dev/null | tr ' ' '\n' | grep "^AI_TASK_TIMEOUT_SECONDS=" | cut -d= -f2 || echo "未检测到")
    
    echo ""
    echo "新环境变量："
    echo "─────────────────────────────────────"
    echo "  CLAUDE_MAX_TURNS=$NEW_MAX_TURNS"
    echo "  AI_TASK_TIMEOUT_SECONDS=$NEW_TIMEOUT"
    
    if [ "$NEW_MAX_TURNS" = "$CLAUDE_MAX_TURNS" ]; then
      success "配置已成功应用！"
    else
      warning "配置可能未完全生效，请检查"
    fi
  else
    error "服务重启失败"
    echo ""
    echo "查看错误日志："
    echo "  journalctl -u ai-telegram-bot.service -n 30"
    exit 1
  fi
else
  success "配置已是最新，无需重启"
fi

echo ""
echo "╔════════════════════════════════════════════════════════════╗"
echo "║  🎉 修复完成！                                            ║"
echo "╚════════════════════════════════════════════════════════════╝"
echo ""
echo "预期改善："
echo "  • 任务不再无限循环（限制 50 轮）"
echo "  • 更快的超时反馈（1 小时而非 2 小时）"
echo "  • 更高的成功率（API 重试 8 次）"
echo ""
echo "现在可以通过 Telegram 测试稳定性！"
echo ""
