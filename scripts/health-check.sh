#!/bin/bash
# Health check script for FFC-AI system stability monitoring

set -e

STATE_DIR="${AI_STATE_ROOT:-/srv/ai-state}"
LOG_FILE="$STATE_DIR/health-check.log"
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

echo "[$TIMESTAMP] FFC-AI 健康检查开始" | tee -a "$LOG_FILE"

# 检查1: 进程运行状态
echo "检查 systemd 服务状态..." | tee -a "$LOG_FILE"
if systemctl is-active --quiet ai-telegram-bot; then
    echo "✓ Telegram Bot 服务运行正常" | tee -a "$LOG_FILE"
else
    echo "✗ Telegram Bot 服务未运行" | tee -a "$LOG_FILE"
fi

# 检查2: 文件描述符泄漏
echo "检查文件描述符使用..." | tee -a "$LOG_FILE"
if pgrep -f "python.*telegram" > /dev/null; then
    PID=$(pgrep -f "python.*telegram" | head -1)
    FD_COUNT=$(ls -la /proc/$PID/fd 2>/dev/null | wc -l)
    echo "  Telegram Bot PID: $PID, 打开的FD数: $FD_COUNT" | tee -a "$LOG_FILE"
    if [ "$FD_COUNT" -gt 500 ]; then
        echo "✗ 警告: 文件描述符数量过多 ($FD_COUNT)" | tee -a "$LOG_FILE"
    else
        echo "✓ 文件描述符使用正常" | tee -a "$LOG_FILE"
    fi
fi

# 检查3: 内存使用
echo "检查内存使用..." | tee -a "$LOG_FILE"
if pgrep -f "python.*telegram" > /dev/null; then
    PID=$(pgrep -f "python.*telegram" | head -1)
    MEM_MB=$(ps -p $PID -o rss= 2>/dev/null | awk '{print int($1/1024)}')
    echo "  内存使用: ${MEM_MB}MB" | tee -a "$LOG_FILE"
    if [ "$MEM_MB" -gt 1024 ]; then
        echo "✗ 警告: 内存使用过高 (${MEM_MB}MB)" | tee -a "$LOG_FILE"
    else
        echo "✓ 内存使用正常" | tee -a "$LOG_FILE"
    fi
fi

# 检查4: 线程数量
echo "检查线程数量..." | tee -a "$LOG_FILE"
if pgrep -f "python.*telegram" > /dev/null; then
    PID=$(pgrep -f "python.*telegram" | head -1)
    THREAD_COUNT=$(ps -p $PID -o nlwp= 2>/dev/null)
    echo "  线程数: $THREAD_COUNT" | tee -a "$LOG_FILE"
    if [ "$THREAD_COUNT" -gt 100 ]; then
        echo "✗ 警告: 线程数量异常 ($THREAD_COUNT)" | tee -a "$LOG_FILE"
    else
        echo "✓ 线程数量正常" | tee -a "$LOG_FILE"
    fi
fi

# 检查5: 僵尸进程
echo "检查僵尸进程..." | tee -a "$LOG_FILE"
ZOMBIE_COUNT=$(ps aux | awk '$8=="Z"' | wc -l)
if [ "$ZOMBIE_COUNT" -gt 0 ]; then
    echo "✗ 发现 $ZOMBIE_COUNT 个僵尸进程" | tee -a "$LOG_FILE"
    ps aux | awk '$8=="Z"' | tee -a "$LOG_FILE"
else
    echo "✓ 无僵尸进程" | tee -a "$LOG_FILE"
fi

# 检查6: 日志文件大小
echo "检查日志文件大小..." | tee -a "$LOG_FILE"
if [ -f "/var/log/ai-telegram-bot.log" ]; then
    LOG_SIZE_MB=$(du -m /var/log/ai-telegram-bot.log | cut -f1)
    echo "  日志大小: ${LOG_SIZE_MB}MB" | tee -a "$LOG_FILE"
    if [ "$LOG_SIZE_MB" -gt 1000 ]; then
        echo "✗ 警告: 日志文件过大，建议轮转" | tee -a "$LOG_FILE"
    else
        echo "✓ 日志大小正常" | tee -a "$LOG_FILE"
    fi
fi

# 检查7: 磁盘空间
echo "检查磁盘空间..." | tee -a "$LOG_FILE"
DISK_USAGE=$(df -h "$STATE_DIR" | tail -1 | awk '{print $5}' | sed 's/%//')
echo "  $STATE_DIR 使用率: ${DISK_USAGE}%" | tee -a "$LOG_FILE"
if [ "$DISK_USAGE" -gt 90 ]; then
    echo "✗ 警告: 磁盘空间不足" | tee -a "$LOG_FILE"
else
    echo "✓ 磁盘空间充足" | tee -a "$LOG_FILE"
fi

# 检查8: 网络连接状态
echo "检查网络连接..." | tee -a "$LOG_FILE"
if timeout 5 curl -s https://api.telegram.org > /dev/null 2>&1; then
    echo "✓ Telegram API 可达" | tee -a "$LOG_FILE"
else
    echo "✗ Telegram API 不可达" | tee -a "$LOG_FILE"
fi

# 检查9: Codex配置
echo "检查 Codex 配置..." | tee -a "$LOG_FILE"
CODEX_CONFIG="$HOME/.config/codex/model_providers.toml"
if [ -f "$CODEX_CONFIG" ]; then
    if grep -q 'wire_api = "responses"' "$CODEX_CONFIG"; then
        echo "✓ Codex 已配置使用稳定的 HTTP API" | tee -a "$LOG_FILE"
    else
        echo "⚠ Codex 未配置 wire_api=responses，可能遇到连接问题" | tee -a "$LOG_FILE"
    fi
else
    echo "⚠ Codex 配置文件不存在" | tee -a "$LOG_FILE"
fi

# 检查10: 错误日志
echo "检查最近的错误..." | tee -a "$LOG_FILE"
if [ -f "$STATE_DIR/telegram-poll-failures.jsonl" ]; then
    RECENT_ERRORS=$(tail -5 "$STATE_DIR/telegram-poll-failures.jsonl" 2>/dev/null | wc -l)
    if [ "$RECENT_ERRORS" -gt 0 ]; then
        echo "⚠ 发现 $RECENT_ERRORS 条最近的 Telegram 轮询错误" | tee -a "$LOG_FILE"
        tail -3 "$STATE_DIR/telegram-poll-failures.jsonl" 2>/dev/null | tee -a "$LOG_FILE"
    else
        echo "✓ 无最近错误" | tee -a "$LOG_FILE"
    fi
fi

echo "[$TIMESTAMP] 健康检查完成" | tee -a "$LOG_FILE"
echo "" >> "$LOG_FILE"

# 返回0表示正常，1表示有警告
if grep -q "✗" "$LOG_FILE" | tail -20; then
    exit 1
fi
exit 0
