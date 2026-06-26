---

## ⚠️ 重要：稳定性修复 / Important: Stability Fix

**如果你遇到以下问题：**
- Claude Code 任务频繁中断
- 任务长时间运行后断开
- 出现 "执行失败" 错误
- 任务卡在 "calling_model" 状态

**快速修复：**

```bash
cd /path/to/FFC-AI
bash scripts/quick-fix-claude-stability.sh
```

这会自动优化配置并重启服务，预计成功率从 40-80% 提升到 70-95%。

**详细信息：** 查看 [CLAUDE_CODE_STABILITY_FIX.md](CLAUDE_CODE_STABILITY_FIX.md)

---
