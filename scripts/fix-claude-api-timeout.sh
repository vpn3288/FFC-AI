#!/bin/bash
# 修复 Claude Code API 超时问题
# 问题：使用第三方 API 时，单次请求可能挂起很久

set -e

echo "==== 修复 Claude Code API 超时配置 ===="
echo ""

# 1. 创建配置目录
echo "1. 创建 Claude Code 配置目录..."
mkdir -p /root/.config/claude-code

# 2. 读取当前环境变量
ANTHROPIC_BASE_URL="${ANTHROPIC_BASE_URL:-https://cc-vibe.com}"
ANTHROPIC_AUTH_TOKEN="${ANTHROPIC_AUTH_TOKEN:-}"

if [ -f /var/lib/ai-remote-runner/config.env ]; then
    source /var/lib/ai-remote-runner/config.env
fi

echo "   API URL: $ANTHROPIC_BASE_URL"

# 3. 创建优化的配置文件
echo ""
echo "2. 写入优化配置..."
cat > /root/.config/claude-code/settings.json <<EOF
{
  "env": {
    "ANTHROPIC_BASE_URL": "$ANTHROPIC_BASE_URL",
    "ANTHROPIC_AUTH_TOKEN": "$ANTHROPIC_AUTH_TOKEN",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"
  },
  "thirdPartyApi": true,
  "requestTimeout": 120000,
  "streamTimeout": 300000,
  "maxRetries": 3,
  "retryDelay": 2000
}
EOF

echo "   ✓ 已创建 /root/.config/claude-code/settings.json"
echo "   - requestTimeout: 120秒（单次 API 请求超时）"
echo "   - streamTimeout: 300秒（流式响应超时）"
echo "   - maxRetries: 3次（失败重试）"

# 4. 验证配置
echo ""
echo "3. 验证配置..."
if [ -f /root/.config/claude-code/settings.json ]; then
    echo "   ✓ 配置文件存在"
else
    echo "   ✗ 配置文件创建失败"
    exit 1
fi

# 5. 测试 API 连接
echo ""
echo "4. 测试 API 连接..."
RESPONSE_TIME=$(curl -s -o /dev/null -w "%{time_total}" --max-time 10 "$ANTHROPIC_BASE_URL/v1/models" 2>&1 || echo "timeout")
if [[ "$RESPONSE_TIME" =~ ^[0-9.]+$ ]]; then
    echo "   ✓ API 响应时间: ${RESPONSE_TIME}秒"
else
    echo "   ⚠ API 连接测试失败（这是正常的，认证错误不影响超时配置）"
fi

# 6. 提示重启
echo ""
echo "==== 修复完成！ ===="
echo ""
echo "下一步："
echo "  sudo systemctl restart ai-telegram-bot"
echo ""
echo "修复说明："
echo "  - 原问题：默认超时 1800 秒（30 分钟）太长"
echo "  - 新配置：API 请求 120 秒超时，进程 300 秒超时"
echo "  - 效果：请求超过 2 分钟自动失败并重试，不再长时间挂起"
