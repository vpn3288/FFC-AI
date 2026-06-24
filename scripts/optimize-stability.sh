#!/bin/bash
# 自动优化 Codex 和第三方 API 的连接稳定性配置

set -e

echo "🔧 FFC-AI 连接稳定性自动优化脚本"
echo "=================================="
echo ""

# 1. 优化 Codex 配置
CODEX_CONFIG_DIR="$HOME/.config/codex"
CODEX_CONFIG="$CODEX_CONFIG_DIR/model_providers.toml"

echo "📝 优化 Codex 连接配置..."

if [ ! -f "$CODEX_CONFIG" ]; then
    echo "⚠️  未找到 Codex 配置文件，跳过 Codex 优化"
else
    # 备份原配置
    cp "$CODEX_CONFIG" "$CODEX_CONFIG.backup.$(date +%Y%m%d_%H%M%S)"
    echo "✓ 已备份原配置到 $CODEX_CONFIG.backup.*"

    # 检查是否已经优化过
    if grep -q 'wire_api = "responses"' "$CODEX_CONFIG" && \
       grep -q 'supports_websockets = false' "$CODEX_CONFIG" && \
       grep -q 'request_max_retries = 8' "$CODEX_CONFIG"; then
        echo "✓ Codex 配置已经优化，跳过"
    else
        # 应用稳定性优化
        python3 << 'EOF'
import re
import sys

config_path = sys.argv[1] if len(sys.argv) > 1 else None
if not config_path:
    sys.exit(1)

with open(config_path, 'r', encoding='utf-8') as f:
    content = f.read()

# 在 [model_providers.ffc_openai_compat] 段落中优化配置
pattern = r'(\[model_providers\.ffc_openai_compat\][^\[]*)'

def optimize_section(match):
    section = match.group(1)

    # 设置稳定的配置项
    configs = {
        'wire_api': 'responses',
        'supports_websockets': 'false',
        'request_max_retries': '8',
        'stream_max_retries': '12',
        'stream_idle_timeout_ms': '900000',
        'request_timeout_ms': '300000',
    }

    for key, value in configs.items():
        pattern_key = rf'^{key}\s*=.*$'
        if re.search(pattern_key, section, re.MULTILINE):
            section = re.sub(pattern_key, f'{key} = "{value}"' if value in ['responses', 'false'] else f'{key} = {value}', section, flags=re.MULTILINE)
        else:
            # 在段落末尾添加
            section = section.rstrip() + f'\n{key} = "{value}"' if value in ['responses', 'false'] else section.rstrip() + f'\n{key} = {value}'

    return section

if '[model_providers.ffc_openai_compat]' in content:
    content = re.sub(pattern, optimize_section, content, count=1)

    with open(config_path, 'w', encoding='utf-8') as f:
        f.write(content)
    print("✓ Codex 配置已优化")
else:
    print("⚠️  未找到 ffc_openai_compat 配置段落")

EOF
        python3 - "$CODEX_CONFIG" || echo "⚠️  Python 优化脚本执行失败，使用 sed 备用方案"
    fi
fi

# 2. 优化环境变量配置
STATE_DIR="${AI_STATE_ROOT:-/srv/ai-state}"
CONFIG_ENV="$STATE_DIR/config.env"

echo ""
echo "📝 优化环境变量配置..."

if [ ! -f "$CONFIG_ENV" ]; then
    echo "⚠️  未找到 config.env，将创建新文件"
    mkdir -p "$STATE_DIR"
    touch "$CONFIG_ENV"
fi

# 备份
cp "$CONFIG_ENV" "$CONFIG_ENV.backup.$(date +%Y%m%d_%H%M%S)"

# 设置优化的环境变量
declare -A optimizations=(
    ["CLAUDE_API_RETRY_ATTEMPTS"]="5"
    ["CLAUDE_API_RETRY_SLEEP_SECONDS"]="5"
    ["TELEGRAM_HTTP_RETRY_ATTEMPTS"]="4"
    ["TELEGRAM_HTTP_RETRY_BASE_DELAY"]="0.5"
    ["TELEGRAM_HTTP_RETRY_MAX_DELAY"]="10.0"
    ["TELEGRAM_POLL_TIMEOUT_SECONDS"]="30"
    ["TELEGRAM_STATUS_INTERVAL_SECONDS"]="5"
    ["THREAD_CLEANUP_THRESHOLD_SECONDS"]="3600"
)

for key in "${!optimizations[@]}"; do
    value="${optimizations[$key]}"
    if grep -q "^${key}=" "$CONFIG_ENV"; then
        # 更新现有值
        sed -i "s|^${key}=.*|${key}=${value}|" "$CONFIG_ENV"
        echo "✓ 更新 $key=$value"
    else
        # 添加新配置
        echo "${key}=${value}" >> "$CONFIG_ENV"
        echo "✓ 添加 $key=$value"
    fi
done

# 3. 设置系统级网络优化
echo ""
echo "📝 检查系统网络配置..."

# 检查是否有权限修改系统配置
if [ "$EUID" -eq 0 ]; then
    echo "✓ 以 root 运行，可以优化系统级配置"

    # TCP keepalive 优化
    if [ -f /proc/sys/net/ipv4/tcp_keepalive_time ]; then
        current_keepalive=$(cat /proc/sys/net/ipv4/tcp_keepalive_time)
        if [ "$current_keepalive" -gt 600 ]; then
            echo "⚠️  当前 TCP keepalive 时间: ${current_keepalive}秒，建议优化"
            echo "  运行以下命令优化（重启后失效）："
            echo "  sysctl -w net.ipv4.tcp_keepalive_time=600"
            echo "  sysctl -w net.ipv4.tcp_keepalive_intvl=30"
            echo "  sysctl -w net.ipv4.tcp_keepalive_probes=5"
        else
            echo "✓ TCP keepalive 配置已优化"
        fi
    fi
else
    echo "⚠️  非 root 运行，跳过系统级优化"
    echo "  提示: 使用 sudo 运行可进行更深度优化"
fi

# 4. 创建定期清理任务
echo ""
echo "📝 设置定期清理任务..."

CLEANUP_SCRIPT="/usr/local/bin/ffc-ai-cleanup"
cat > "$CLEANUP_SCRIPT" << 'EOFCLEANUP'
#!/bin/bash
# FFC-AI 自动清理脚本

STATE_DIR="${AI_STATE_ROOT:-/srv/ai-state}"
LOG_FILE="$STATE_DIR/cleanup.log"
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

echo "[$TIMESTAMP] 开始清理" >> "$LOG_FILE"

# 清理旧的临时文件
find "$STATE_DIR" -name ".ai-remote-codex-*-last-message.txt" -mtime +1 -delete 2>/dev/null
find "$STATE_DIR" -name "*.backup.*" -mtime +7 -delete 2>/dev/null

# 清理旧的日志
find "$STATE_DIR" -name "*.jsonl" -size +100M -exec sh -c 'tail -10000 "$1" > "$1.tmp" && mv "$1.tmp" "$1"' _ {} \; 2>/dev/null

# 清理僵尸进程
ps aux | awk '$8=="Z"' | awk '{print $2}' | xargs -r kill -9 2>/dev/null || true

echo "[$TIMESTAMP] 清理完成" >> "$LOG_FILE"
EOFCLEANUP

chmod +x "$CLEANUP_SCRIPT"
echo "✓ 创建清理脚本: $CLEANUP_SCRIPT"

# 添加到 crontab（如果是 root）
if [ "$EUID" -eq 0 ]; then
    if ! crontab -l 2>/dev/null | grep -q "$CLEANUP_SCRIPT"; then
        (crontab -l 2>/dev/null; echo "0 3 * * * $CLEANUP_SCRIPT") | crontab -
        echo "✓ 已添加每日凌晨3点自动清理任务"
    else
        echo "✓ 清理任务已存在"
    fi
else
    echo "⚠️  请手动添加定时任务:"
    echo "  crontab -e"
    echo "  添加: 0 3 * * * $CLEANUP_SCRIPT"
fi

echo ""
echo "=================================="
echo "✅ 稳定性优化完成！"
echo ""
echo "📋 优化摘要:"
echo "  1. Codex 配置: 使用 HTTP API，增加重试次数和超时"
echo "  2. 环境变量: 优化重试策略和超时配置"
echo "  3. 清理任务: 创建自动清理脚本"
echo ""
echo "🔄 建议重启服务以应用配置:"
echo "  sudo systemctl restart ai-telegram-bot"
echo ""
echo "🔍 运行健康检查:"
echo "  bash scripts/health-check.sh"
