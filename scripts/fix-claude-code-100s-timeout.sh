#!/usr/bin/env bash
#
# 修复 Claude Code 100秒超时问题的深度优化脚本
# 针对第三方 API (cc-vibe.com) 的特殊优化
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "===== Claude Code 100秒超时深度修复 ====="
echo "目标：解决第三方 API 调用时的长时间等待问题"
echo ""

# 1. 备份当前配置
echo "[1/6] 备份现有配置..."
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
if [ -f /root/.claude/settings.json ]; then
    cp /root/.claude/settings.json "/root/.claude/settings.json.backup_${TIMESTAMP}"
    echo "✓ 已备份 settings.json"
fi

# 2. 优化 Claude CLI 配置 - 更激进的超时设置
echo "[2/6] 优化 Claude CLI 配置（超时、重试、流式）..."
cat > /root/.claude/settings.json <<'EOF'
{
  "env": {
    "ANTHROPIC_BASE_URL": "https://cc-vibe.com",
    "ANTHROPIC_AUTH_TOKEN": "sk-7d5e65dcaf54e6cfa4cb7149cbd1385c43b89b7f64da1f2e6723eb752644d1d0",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    "ANTHROPIC_REQUEST_TIMEOUT": "120000",
    "ANTHROPIC_STREAM_TIMEOUT": "120000"
  },
  "thirdPartyApi": true,
  "requestTimeout": 120000,
  "maxRetries": 8,
  "streamTimeout": 120000,
  "retryDelay": 2000,
  "connectionTimeout": 30000
}
EOF
echo "✓ 已优化 settings.json（120秒超时，8次重试）"

# 3. 优化环境变量 - 缩短重试延迟
echo "[3/6] 优化环境变量..."

# 检查是否有 .env 文件
ENV_FILE="${PROJECT_ROOT}/.env"
if [ ! -f "$ENV_FILE" ]; then
    echo "警告：.env 文件不存在，跳过环境变量优化"
else
    # 备份 .env
    cp "$ENV_FILE" "${ENV_FILE}.backup_${TIMESTAMP}"

    # 更新或添加配置
    declare -A env_vars=(
        ["CLAUDE_API_RETRY_ATTEMPTS"]="8"
        ["CLAUDE_API_RETRY_SLEEP_SECONDS"]="3"
        ["CLAUDE_MAX_TURNS"]="50"
    )

    for key in "${!env_vars[@]}"; do
        value="${env_vars[$key]}"
        if grep -q "^${key}=" "$ENV_FILE"; then
            sed -i "s/^${key}=.*/${key}=${value}/" "$ENV_FILE"
            echo "  更新 ${key}=${value}"
        else
            echo "${key}=${value}" >> "$ENV_FILE"
            echo "  添加 ${key}=${value}"
        fi
    done
    echo "✓ 已优化 .env 配置"
fi

# 4. 设置系统级环境变量
echo "[4/6] 设置系统级环境变量..."
cat > /etc/profile.d/claude-code-timeout-fix.sh <<'EOF'
# Claude Code 第三方 API 超时优化
export CLAUDE_API_RETRY_ATTEMPTS=8
export CLAUDE_API_RETRY_SLEEP_SECONDS=3
export CLAUDE_MAX_TURNS=50
export ANTHROPIC_REQUEST_TIMEOUT=120000
export ANTHROPIC_STREAM_TIMEOUT=120000
EOF
chmod +x /etc/profile.d/claude-code-timeout-fix.sh
echo "✓ 已创建 /etc/profile.d/claude-code-timeout-fix.sh"

# 5. 更新 systemd 服务配置
echo "[5/6] 更新 systemd 服务配置..."
if [ -f /etc/systemd/system/ai-telegram-bot.service ]; then
    # 检查是否已经有环境变量配置
    if ! grep -q "CLAUDE_API_RETRY_ATTEMPTS" /etc/systemd/system/ai-telegram-bot.service; then
        # 在 [Service] 段落添加环境变量
        sed -i '/\[Service\]/a Environment="CLAUDE_API_RETRY_ATTEMPTS=8"\nEnvironment="CLAUDE_API_RETRY_SLEEP_SECONDS=3"\nEnvironment="ANTHROPIC_REQUEST_TIMEOUT=120000"\nEnvironment="ANTHROPIC_STREAM_TIMEOUT=120000"' /etc/systemd/system/ai-telegram-bot.service
        systemctl daemon-reload
        echo "✓ 已更新 systemd 服务环境变量"
    else
        echo "  systemd 服务已包含环境变量配置"
    fi
else
    echo "  警告：未找到 systemd 服务文件"
fi

# 6. 优化 Python 代码中的超时处理
echo "[6/6] 检查 Python 代码优化..."
PROVIDERS_FILE="${PROJECT_ROOT}/src/ai_remote_runner/providers.py"

if [ -f "$PROVIDERS_FILE" ]; then
    # 检查是否已经有超时优化
    if grep -q "timeout_seconds: int = 1800" "$PROVIDERS_FILE"; then
        echo "  提示：providers.py 中超时设置为 1800 秒（30分钟）"
        echo "  建议：如果仍有问题，考虑调整为更短的超时"
    fi

    # 检查重试配置
    if grep -q 'CLAUDE_API_RETRY_ATTEMPTS.*"3"' "$PROVIDERS_FILE"; then
        echo "  提示：代码中默认重试次数为 3，环境变量已覆盖为 8"
    fi

    echo "✓ Python 代码检查完成"
else
    echo "  警告：未找到 providers.py 文件"
fi

echo ""
echo "===== 修复完成 ====="
echo ""
echo "优化摘要："
echo "  • Claude CLI 请求超时：180s → 120s（更快失败，更多重试）"
echo "  • 最大重试次数：5 → 8"
echo "  • 重试延迟：5s → 3s（更快重试）"
echo "  • 流式超时：600s → 120s"
echo ""
echo "下一步："
echo "  1. 重启服务："
echo "     sudo systemctl restart ai-telegram-bot"
echo ""
echo "  2. 查看实时日志："
echo "     journalctl -u ai-telegram-bot -f"
echo ""
echo "  3. 如果仍有问题，运行诊断："
echo "     sudo bash ${SCRIPT_DIR}/diagnose-claude-code.sh"
echo ""
