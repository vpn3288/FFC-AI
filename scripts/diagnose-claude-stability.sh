#!/usr/bin/env bash
# Comprehensive diagnostic tool for Claude Code stability issues

set -euo pipefail

STATE_ROOT="${AI_REMOTE_STATE:-${AI_STATE_ROOT:-/var/lib/ai-remote-runner}}"
CONFIG_ENV="$STATE_ROOT/config.env"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

check_config() {
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "检查配置文件 / Checking Configuration"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  
  if [ ! -f "$CONFIG_ENV" ]; then
    printf "${RED}✗${NC} Config file not found: $CONFIG_ENV\n"
    return 1
  fi
  
  printf "${GREEN}✓${NC} Config file exists: $CONFIG_ENV\n\n"
  
  # Check critical settings
  local max_turns=$(grep "^CLAUDE_MAX_TURNS=" "$CONFIG_ENV" 2>/dev/null | cut -d= -f2 || echo "NOT_SET")
  local timeout=$(grep "^AI_TASK_TIMEOUT_SECONDS=" "$CONFIG_ENV" 2>/dev/null | cut -d= -f2 || echo "NOT_SET")
  local retry=$(grep "^CLAUDE_API_RETRY_ATTEMPTS=" "$CONFIG_ENV" 2>/dev/null | cut -d= -f2 || echo "NOT_SET")
  local exec_timeout=$(grep "^AI_LOCAL_EXEC_TIMEOUT_SECONDS=" "$CONFIG_ENV" 2>/dev/null | cut -d= -f2 || echo "NOT_SET")
  
  echo "关键配置 / Critical Settings:"
  
  # CLAUDE_MAX_TURNS
  if [ "$max_turns" = "0" ] || [ "$max_turns" = "NOT_SET" ]; then
    printf "  ${RED}✗ CLAUDE_MAX_TURNS=${NC} %s ${RED}(会导致无限循环!)${NC}\n" "$max_turns"
  elif [ "$max_turns" -gt 100 ]; then
    printf "  ${YELLOW}! CLAUDE_MAX_TURNS=${NC} %s ${YELLOW}(可能太大)${NC}\n" "$max_turns"
  else
    printf "  ${GREEN}✓ CLAUDE_MAX_TURNS=${NC} %s\n" "$max_turns"
  fi
  
  # AI_TASK_TIMEOUT_SECONDS
  if [ "$timeout" = "NOT_SET" ]; then
    printf "  ${YELLOW}! AI_TASK_TIMEOUT_SECONDS=${NC} NOT_SET\n"
  elif [ "$timeout" -gt 7200 ]; then
    printf "  ${YELLOW}! AI_TASK_TIMEOUT_SECONDS=${NC} %s ${YELLOW}(太长了)${NC}\n" "$timeout"
  else
    printf "  ${GREEN}✓ AI_TASK_TIMEOUT_SECONDS=${NC} %s\n" "$timeout"
  fi
  
  # CLAUDE_API_RETRY_ATTEMPTS
  if [ "$retry" = "NOT_SET" ]; then
    printf "  ${YELLOW}! CLAUDE_API_RETRY_ATTEMPTS=${NC} NOT_SET\n"
  elif [ "$retry" -lt 5 ]; then
    printf "  ${YELLOW}! CLAUDE_API_RETRY_ATTEMPTS=${NC} %s ${YELLOW}(可能太少)${NC}\n" "$retry"
  else
    printf "  ${GREEN}✓ CLAUDE_API_RETRY_ATTEMPTS=${NC} %s\n" "$retry"
  fi
  
  # AI_LOCAL_EXEC_TIMEOUT_SECONDS
  if [ "$exec_timeout" = "NOT_SET" ]; then
    printf "  ${YELLOW}! AI_LOCAL_EXEC_TIMEOUT_SECONDS=${NC} NOT_SET\n"
  elif [ "$exec_timeout" -lt 300 ]; then
    printf "  ${YELLOW}! AI_LOCAL_EXEC_TIMEOUT_SECONDS=${NC} %s ${YELLOW}(可能太短)${NC}\n" "$exec_timeout"
  else
    printf "  ${GREEN}✓ AI_LOCAL_EXEC_TIMEOUT_SECONDS=${NC} %s\n" "$exec_timeout"
  fi
  
  echo ""
}

check_services() {
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "检查服务状态 / Checking Service Status"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  
  for service in ai-telegram-bot ai-remote-runner; do
    if systemctl is-active --quiet "$service"; then
      printf "${GREEN}✓${NC} %-25s ${GREEN}running${NC}\n" "$service"
      local uptime=$(systemctl show -p ActiveEnterTimestamp "$service" --value)
      echo "   Started: $uptime"
    else
      printf "${RED}✗${NC} %-25s ${RED}stopped${NC}\n" "$service"
    fi
  done
  echo ""
}

check_claude_code() {
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "检查 Claude Code / Checking Claude Code"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  
  if command -v claude &> /dev/null; then
    local version=$(claude --version 2>&1 | head -1)
    printf "${GREEN}✓${NC} Claude Code installed: %s\n" "$version"
  else
    printf "${RED}✗${NC} Claude Code not found\n"
  fi
  echo ""
}

check_recent_errors() {
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "最近错误 / Recent Errors (last 10)"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  
  journalctl -u ai-telegram-bot --since "1 hour ago" --no-pager | grep -i "error\|failed\|exception" | tail -10 || echo "No recent errors found"
  echo ""
}

show_recommendations() {
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "建议 / Recommendations"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  
  local needs_fix=false
  
  if [ -f "$CONFIG_ENV" ]; then
    local max_turns=$(grep "^CLAUDE_MAX_TURNS=" "$CONFIG_ENV" 2>/dev/null | cut -d= -f2 || echo "0")
    
    if [ "$max_turns" = "0" ]; then
      echo "⚠️  CRITICAL: CLAUDE_MAX_TURNS=0 会导致无限循环!"
      echo "   运行: bash scripts/quick-fix-claude-stability.sh"
      needs_fix=true
    fi
  fi
  
  if ! $needs_fix; then
    echo "✓ 配置看起来正常"
    echo "  如果仍有问题，请查看日志:"
    echo "  journalctl -u ai-telegram-bot -f"
  fi
  
  echo ""
}

main() {
  echo ""
  echo "═══════════════════════════════════════════════════════════"
  echo "       Claude Code 稳定性诊断 / Stability Diagnostic"
  echo "═══════════════════════════════════════════════════════════"
  echo ""
  
  check_config
  check_services
  check_claude_code
  check_recent_errors
  show_recommendations
}

main "$@"
