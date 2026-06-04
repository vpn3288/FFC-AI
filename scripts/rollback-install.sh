#!/usr/bin/env bash
set -euo pipefail

STATE_ROOT="${AI_REMOTE_STATE:-/var/lib/ai-remote-runner}"
INSTALL_ROOT="${AI_REMOTE_INSTALL_ROOT:-/opt/ai-remote-runner}"

printf '[rollback] stopping service when present\n'
if command -v systemctl >/dev/null 2>&1 && systemctl list-unit-files ai-remote-runner.service >/dev/null 2>&1; then
  sudo systemctl stop ai-remote-runner.service || true
  sudo systemctl disable ai-remote-runner.service || true
  sudo rm -f /etc/systemd/system/ai-remote-runner.service
  sudo systemctl daemon-reload || true
fi

printf '[rollback] preserving workspaces and credentials by default\n'
sudo rm -f "$INSTALL_ROOT/run-local.sh"
sudo rm -f "$STATE_ROOT/config.env"
printf '[rollback] complete; credential store and workspaces were not deleted\n'
