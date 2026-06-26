# Restart After Active Tasks

If configuration changes require systemd services to reload environment variables, wait until there are no active AI tasks, then run from an SSH terminal:

```bash
sudo systemctl restart ai-telegram-bot.service
```

Do not run that command from inside a Telegram AI task. Restarting `ai-telegram-bot.service` from its own cgroup terminates the active Claude Code process and can leave systemd waiting on a self-restart job.

To check whether a restart is needed without restarting anything:

```bash
cd /root/FFC-AI
bash scripts/verify-and-restart.sh
```

If a restart is needed, the script writes `/var/lib/ai-remote-runner/pending-service-restart.txt`.
