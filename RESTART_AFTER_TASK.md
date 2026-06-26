# ⚠️ 重要：任务完成后执行

当前 AI 任务完成后，请立即执行以下命令使配置生效：

```bash
sudo systemctl restart ai-telegram-bot.service
```

或者使用自动验证脚本：

```bash
cd /root/FFC-AI
bash scripts/fix-stability-restart.sh
```

## 为什么需要手动重启？

根据系统安全规则，AI 任务**不能在执行过程中重启自己所在的服务**，这会导致：
- 当前任务被强制中断 (returncode=143)
- 输出丢失
- Telegram 消息无法发送

因此，配置虽然已优化，但需要在任务完成后手动重启才能生效。

## 验证修复成功

重启后运行：

```bash
ps eww $(pgrep -f "ai_remote_runner.cli telegram" | head -1) | tr ' ' '\n' | grep CLAUDE_MAX_TURNS
```

**期望输出：** `CLAUDE_MAX_TURNS=50` ✅  
**错误输出：** `CLAUDE_MAX_TURNS=0` ❌

## 当前状态

✅ 配置文件已优化 (`/var/lib/ai-remote-runner/config.env`)
✅ 修复脚本已创建 (`scripts/fix-stability-restart.sh`)
✅ 代码已推送到 GitHub (https://github.com/vpn3288/FFC-AI)
⏳ 等待手动重启使配置生效

---

**下次执行 AI 任务前，请务必先重启服务！**
