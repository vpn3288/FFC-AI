#!/bin/bash
# Detailed Claude Code diagnostic tool
# Checks all aspects of Claude Code configuration and connectivity

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}╔════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║    Claude Code 详细诊断工具                                ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════════════════════════╝${NC}\n"

CONFIG_FILE="/var/lib/ai-remote-runner/config.env"
PASS=0
FAIL=0
WARN=0

check_pass() {
    echo -e "${GREEN}✓${NC} $1"
    PASS=$((PASS + 1))
}

check_fail() {
    echo -e "${RED}✗${NC} $1"
    FAIL=$((FAIL + 1))
}

check_warn() {
    echo -e "${YELLOW}⚠${NC} $1"
    WARN=$((WARN + 1))
}

# 1. Check Claude Code installation
echo -e "${BLUE}[1/10] Claude Code 安装检查${NC}"
if command -v claude &> /dev/null; then
    VERSION=$(claude --version 2>&1 || echo "unknown")
    check_pass "Claude Code 已安装: $VERSION"
else
    check_fail "Claude Code 未安装"
fi
echo ""

# 2. Check authentication
echo -e "${BLUE}[2/10] 认证状态检查${NC}"
AUTH_STATUS=$(claude auth status --json 2>&1 || echo "{}")
if echo "$AUTH_STATUS" | grep -q "loggedIn.*true"; then
    check_pass "已登录 Claude Code"
    API_PROVIDER=$(echo "$AUTH_STATUS" | grep -o '"apiProvider":"[^"]*"' | cut -d'"' -f4)
    if [ "$API_PROVIDER" = "firstParty" ]; then
        check_warn "API Provider: firstParty (可能未使用第三方 API)"
    else
        check_pass "API Provider: $API_PROVIDER"
    fi
else
    check_fail "未登录 Claude Code"
fi
echo ""

# 3. Check config file
echo -e "${BLUE}[3/10] 配置文件检查${NC}"
if [ -f "$CONFIG_FILE" ]; then
    check_pass "配置文件存在: $CONFIG_FILE"
else
    check_fail "配置文件不存在: $CONFIG_FILE"
    echo -e "\n${RED}错误: 无法继续诊断，配置文件缺失${NC}\n"
    exit 1
fi
echo ""

# Source config
source "$CONFIG_FILE"

# 4. Check third-party API settings
echo -e "${BLUE}[4/10] 第三方 API 配置检查${NC}"
if [ -n "$ANTHROPIC_BASE_URL" ]; then
    check_pass "ANTHROPIC_BASE_URL: $ANTHROPIC_BASE_URL"
else
    check_fail "ANTHROPIC_BASE_URL 未设置"
fi

if [ -n "$ANTHROPIC_AUTH_TOKEN" ]; then
    check_pass "ANTHROPIC_AUTH_TOKEN: ${ANTHROPIC_AUTH_TOKEN:0:15}..."
else
    check_fail "ANTHROPIC_AUTH_TOKEN 未设置"
fi

if [ -n "$ANTHROPIC_API_URL" ]; then
    check_pass "ANTHROPIC_API_URL: $ANTHROPIC_API_URL"
else
    check_warn "ANTHROPIC_API_URL 未设置（建议添加）"
fi

if [ -n "$ANTHROPIC_API_KEY" ]; then
    check_pass "ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY:0:15}..."
else
    check_warn "ANTHROPIC_API_KEY 未设置（建议添加）"
fi
echo ""

# 5. Test API connectivity
echo -e "${BLUE}[5/10] API 连接测试${NC}"
if [ -n "$ANTHROPIC_BASE_URL" ] && [ -n "$ANTHROPIC_AUTH_TOKEN" ]; then
    echo -e "  测试端点: $ANTHROPIC_BASE_URL/v1/messages"

    RESPONSE=$(curl -s -m 15 -w "\n%{http_code}" \
        -H "x-api-key: $ANTHROPIC_AUTH_TOKEN" \
        -H "Content-Type: application/json" \
        -X POST "$ANTHROPIC_BASE_URL/v1/messages" \
        -d '{"model":"claude-sonnet-4-6","max_tokens":10,"messages":[{"role":"user","content":"hi"}]}' 2>&1)

    HTTP_CODE=$(echo "$RESPONSE" | tail -n1)
    BODY=$(echo "$RESPONSE" | head -n-1)

    if [ "$HTTP_CODE" = "200" ]; then
        check_pass "API 连接成功 (HTTP 200)"
    elif [ "$HTTP_CODE" = "000" ]; then
        check_fail "API 连接失败: 网络超时或无法连接"
        echo -e "  ${RED}可能原因: 网络问题、防火墙、API 地址错误${NC}"
    else
        check_warn "API 返回 HTTP $HTTP_CODE"
        echo -e "  响应: ${YELLOW}${BODY:0:100}${NC}"
    fi
else
    check_fail "无法测试: API 配置不完整"
fi
echo ""

# 6. Check timeout settings
echo -e "${BLUE}[6/10] 超时配置检查${NC}"
TASK_TIMEOUT=${AI_TASK_TIMEOUT_SECONDS:-0}
if [ "$TASK_TIMEOUT" -ge 3600 ]; then
    check_pass "AI_TASK_TIMEOUT_SECONDS: $TASK_TIMEOUT 秒"
elif [ "$TASK_TIMEOUT" -gt 0 ]; then
    check_warn "AI_TASK_TIMEOUT_SECONDS: $TASK_TIMEOUT 秒 (建议 >= 3600)"
else
    check_fail "AI_TASK_TIMEOUT_SECONDS 未设置"
fi

LOCAL_TIMEOUT=${AI_LOCAL_EXEC_TIMEOUT_SECONDS:-0}
if [ "$LOCAL_TIMEOUT" -ge 300 ]; then
    check_pass "AI_LOCAL_EXEC_TIMEOUT_SECONDS: $LOCAL_TIMEOUT 秒"
else
    check_warn "AI_LOCAL_EXEC_TIMEOUT_SECONDS: $LOCAL_TIMEOUT 秒 (建议 >= 300)"
fi
echo ""

# 7. Check retry settings
echo -e "${BLUE}[7/10] 重试配置检查${NC}"
RETRY_ATTEMPTS=${CLAUDE_API_RETRY_ATTEMPTS:-0}
if [ "$RETRY_ATTEMPTS" -ge 5 ]; then
    check_pass "CLAUDE_API_RETRY_ATTEMPTS: $RETRY_ATTEMPTS"
else
    check_warn "CLAUDE_API_RETRY_ATTEMPTS: $RETRY_ATTEMPTS (建议 >= 5)"
fi

RETRY_SLEEP=${CLAUDE_API_RETRY_SLEEP_SECONDS:-0}
if [ "$RETRY_SLEEP" -ge 3 ]; then
    check_pass "CLAUDE_API_RETRY_SLEEP_SECONDS: $RETRY_SLEEP"
else
    check_warn "CLAUDE_API_RETRY_SLEEP_SECONDS: $RETRY_SLEEP (建议 >= 3)"
fi
echo ""

# 8. Check service status
echo -e "${BLUE}[8/10] 服务状态检查${NC}"
if systemctl is-active --quiet ai-telegram-bot; then
    check_pass "ai-telegram-bot 服务运行中"

    # Check for stuck processes
    CLAUDE_PROCS=$(pgrep -f "claude -p" | wc -l)
    if [ "$CLAUDE_PROCS" -gt 0 ]; then
        check_warn "检测到 $CLAUDE_PROCS 个 Claude Code 进程"
    fi
else
    check_fail "ai-telegram-bot 服务未运行"
fi
echo ""

# 9. Check for recent errors
echo -e "${BLUE}[9/10] 最近错误检查${NC}"
RECENT_ERRORS=$(journalctl -u ai-telegram-bot --since "10 minutes ago" --no-pager 2>/dev/null | grep -i "error\|failed\|timeout" | wc -l)
if [ "$RECENT_ERRORS" -eq 0 ]; then
    check_pass "最近 10 分钟无错误日志"
else
    check_warn "最近 10 分钟检测到 $RECENT_ERRORS 条错误/失败日志"
fi
echo ""

# 10. Check disk space and memory
echo -e "${BLUE}[10/10] 系统资源检查${NC}"
DISK_USAGE=$(df -h / | awk 'NR==2 {print $5}' | sed 's/%//')
if [ "$DISK_USAGE" -lt 90 ]; then
    check_pass "磁盘使用率: $DISK_USAGE%"
else
    check_warn "磁盘使用率较高: $DISK_USAGE%"
fi

MEM_AVAILABLE=$(free -m | awk 'NR==2 {print $7}')
if [ "$MEM_AVAILABLE" -gt 500 ]; then
    check_pass "可用内存: ${MEM_AVAILABLE}MB"
else
    check_warn "可用内存较低: ${MEM_AVAILABLE}MB"
fi
echo ""

# Summary
echo -e "${BLUE}╔════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║                    诊断结果汇总                             ║${NC}"
echo -e "${BLUE}╠════════════════════════════════════════════════════════════╣${NC}"
echo -e "${BLUE}║${NC}  ${GREEN}通过: $PASS${NC}  ${YELLOW}警告: $WARN${NC}  ${RED}失败: $FAIL${NC}"
echo -e "${BLUE}╚════════════════════════════════════════════════════════════╝${NC}\n"

if [ "$FAIL" -gt 0 ]; then
    echo -e "${RED}发现严重问题，建议运行修复脚本：${NC}"
    echo -e "${GREEN}sudo bash /root/FFC-AI/scripts/fix-claude-code-third-party-api.sh${NC}\n"
    exit 1
elif [ "$WARN" -gt 0 ]; then
    echo -e "${YELLOW}发现一些警告，建议优化配置${NC}\n"
    exit 0
else
    echo -e "${GREEN}所有检查通过！系统配置正常${NC}\n"
    exit 0
fi
