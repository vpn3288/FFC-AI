#!/bin/bash
# Fix Claude Code third-party API connection issues
# This script ensures Claude Code uses the configured third-party API instead of official Anthropic API

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}=== Claude Code 第三方 API 连接修复工具 ===${NC}\n"

CONFIG_FILE="/var/lib/ai-remote-runner/config.env"

# Check if config file exists
if [ ! -f "$CONFIG_FILE" ]; then
    echo -e "${RED}错误: 配置文件不存在: $CONFIG_FILE${NC}"
    exit 1
fi

# Source the config
source "$CONFIG_FILE"

# Check if third-party API is configured
if [ -z "$ANTHROPIC_BASE_URL" ] || [ -z "$ANTHROPIC_AUTH_TOKEN" ]; then
    echo -e "${RED}错误: 未配置第三方 API${NC}"
    echo "请确保在 $CONFIG_FILE 中设置了："
    echo "  - ANTHROPIC_BASE_URL"
    echo "  - ANTHROPIC_AUTH_TOKEN"
    exit 1
fi

echo -e "${GREEN}✓ 检测到第三方 API 配置:${NC}"
echo -e "  BASE_URL: ${BLUE}$ANTHROPIC_BASE_URL${NC}"
echo -e "  AUTH_TOKEN: ${BLUE}${ANTHROPIC_AUTH_TOKEN:0:10}...${NC}\n"

# Test API connectivity
echo -e "${BLUE}测试 API 连接...${NC}"
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" -m 10 \
    -H "x-api-key: $ANTHROPIC_AUTH_TOKEN" \
    -H "Content-Type: application/json" \
    -X POST "$ANTHROPIC_BASE_URL/v1/messages" \
    -d '{"model":"claude-sonnet-4-6","max_tokens":10,"messages":[{"role":"user","content":"test"}]}' 2>&1 || echo "000")

if [ "$HTTP_CODE" = "200" ]; then
    echo -e "${GREEN}✓ API 连接测试成功 (HTTP $HTTP_CODE)${NC}\n"
elif [ "$HTTP_CODE" = "000" ]; then
    echo -e "${RED}✗ API 连接失败: 网络超时或无法连接${NC}"
    echo -e "${YELLOW}  请检查网络连接和 API 地址是否正确${NC}\n"
else
    echo -e "${YELLOW}⚠ API 返回状态码: $HTTP_CODE${NC}"
    echo -e "${YELLOW}  这可能是正常的（某些 API 可能返回非 200 状态码）${NC}\n"
fi

# Update environment variables for Claude Code
echo -e "${BLUE}更新环境变量配置...${NC}"

# Add additional environment variables to ensure Claude Code uses third-party API
if ! grep -q "^ANTHROPIC_API_URL=" "$CONFIG_FILE"; then
    echo "ANTHROPIC_API_URL=$ANTHROPIC_BASE_URL" >> "$CONFIG_FILE"
    echo -e "${GREEN}✓ 添加 ANTHROPIC_API_URL${NC}"
else
    sudo sed -i "s|^ANTHROPIC_API_URL=.*|ANTHROPIC_API_URL=$ANTHROPIC_BASE_URL|" "$CONFIG_FILE"
    echo -e "${GREEN}✓ 更新 ANTHROPIC_API_URL${NC}"
fi

if ! grep -q "^ANTHROPIC_API_KEY=" "$CONFIG_FILE"; then
    echo "ANTHROPIC_API_KEY=$ANTHROPIC_AUTH_TOKEN" >> "$CONFIG_FILE"
    echo -e "${GREEN}✓ 添加 ANTHROPIC_API_KEY${NC}"
else
    sudo sed -i "s|^ANTHROPIC_API_KEY=.*|ANTHROPIC_API_KEY=$ANTHROPIC_AUTH_TOKEN|" "$CONFIG_FILE"
    echo -e "${GREEN}✓ 更新 ANTHROPIC_API_KEY${NC}"
fi

if ! grep -q "^CLAUDE_CODE_DISABLE_OAUTH=" "$CONFIG_FILE"; then
    echo "CLAUDE_CODE_DISABLE_OAUTH=1" >> "$CONFIG_FILE"
    echo -e "${GREEN}✓ 添加 CLAUDE_CODE_DISABLE_OAUTH${NC}"
fi

# Increase timeout settings
echo ""
echo -e "${BLUE}优化超时配置...${NC}"

if ! grep -q "^AI_TASK_TIMEOUT_SECONDS=" "$CONFIG_FILE"; then
    echo "AI_TASK_TIMEOUT_SECONDS=7200" >> "$CONFIG_FILE"
    echo -e "${GREEN}✓ 设置 AI_TASK_TIMEOUT_SECONDS=7200 (2小时)${NC}"
else
    CURRENT_TIMEOUT=$(grep "^AI_TASK_TIMEOUT_SECONDS=" "$CONFIG_FILE" | cut -d'=' -f2)
    if [ "$CURRENT_TIMEOUT" -lt 3600 ]; then
        sudo sed -i "s/^AI_TASK_TIMEOUT_SECONDS=.*/AI_TASK_TIMEOUT_SECONDS=7200/" "$CONFIG_FILE"
        echo -e "${GREEN}✓ 增加 AI_TASK_TIMEOUT_SECONDS 从 $CURRENT_TIMEOUT 到 7200${NC}"
    else
        echo -e "${GREEN}✓ AI_TASK_TIMEOUT_SECONDS 已设置为 $CURRENT_TIMEOUT${NC}"
    fi
fi

# Update retry settings
if ! grep -q "^CLAUDE_API_RETRY_ATTEMPTS=" "$CONFIG_FILE"; then
    echo "CLAUDE_API_RETRY_ATTEMPTS=8" >> "$CONFIG_FILE"
    echo -e "${GREEN}✓ 设置 CLAUDE_API_RETRY_ATTEMPTS=8${NC}"
else
    sudo sed -i "s/^CLAUDE_API_RETRY_ATTEMPTS=.*/CLAUDE_API_RETRY_ATTEMPTS=8/" "$CONFIG_FILE"
    echo -e "${GREEN}✓ 更新 CLAUDE_API_RETRY_ATTEMPTS=8${NC}"
fi

if ! grep -q "^CLAUDE_API_RETRY_SLEEP_SECONDS=" "$CONFIG_FILE"; then
    echo "CLAUDE_API_RETRY_SLEEP_SECONDS=3" >> "$CONFIG_FILE"
    echo -e "${GREEN}✓ 设置 CLAUDE_API_RETRY_SLEEP_SECONDS=3${NC}"
else
    sudo sed -i "s/^CLAUDE_API_RETRY_SLEEP_SECONDS=.*/CLAUDE_API_RETRY_SLEEP_SECONDS=3/" "$CONFIG_FILE"
    echo -e "${GREEN}✓ 更新 CLAUDE_API_RETRY_SLEEP_SECONDS=3${NC}"
fi

echo ""
echo -e "${GREEN}✓ 配置更新完成！${NC}\n"

# Show summary
echo -e "${BLUE}=== 当前配置摘要 ===${NC}"
echo -e "API 端点: ${GREEN}$ANTHROPIC_BASE_URL${NC}"
echo -e "任务超时: ${GREEN}$(grep "^AI_TASK_TIMEOUT_SECONDS=" "$CONFIG_FILE" | cut -d'=' -f2) 秒${NC}"
echo -e "重试次数: ${GREEN}$(grep "^CLAUDE_API_RETRY_ATTEMPTS=" "$CONFIG_FILE" | cut -d'=' -f2)${NC}"
echo -e "重试延迟: ${GREEN}$(grep "^CLAUDE_API_RETRY_SLEEP_SECONDS=" "$CONFIG_FILE" | cut -d'=' -f2) 秒${NC}"

echo ""
echo -e "${YELLOW}=== 下一步操作 ===${NC}"
echo -e "1. 重启服务使配置生效："
echo -e "   ${GREEN}sudo systemctl restart ai-telegram-bot${NC}"
echo ""
echo -e "2. 查看服务状态："
echo -e "   ${GREEN}sudo systemctl status ai-telegram-bot${NC}"
echo ""
echo -e "3. 实时查看日志："
echo -e "   ${GREEN}sudo journalctl -u ai-telegram-bot -f${NC}"
echo ""
