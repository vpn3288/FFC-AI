#!/usr/bin/env bash
set -euo pipefail

STATE_ROOT="${AI_REMOTE_STATE:-/var/lib/ai-remote-runner}"
INSTALL_ROOT="${AI_REMOTE_INSTALL_ROOT:-/opt/ai-remote-runner}"

printf '[rollback] stopping service when present\n'
if [ ! -f "$STATE_ROOT/install-manifest.json" ]; then
  printf '[rollback] warning: %s/install-manifest.json missing; using conservative rollback\n' "$STATE_ROOT"
fi
if command -v systemctl >/dev/null 2>&1 && systemctl list-unit-files ai-remote-runner.service >/dev/null 2>&1; then
  sudo systemctl stop ai-remote-runner.service || true
  sudo systemctl disable ai-remote-runner.service || true
  sudo rm -f /etc/systemd/system/ai-remote-runner.service
  sudo systemctl daemon-reload || true
fi

printf '[rollback] preserving workspaces and credentials by default\n'
sudo rm -f "$INSTALL_ROOT/run-local.sh"
if [ -f "$STATE_ROOT/install-snapshots/config.env.preinstall" ]; then
  sudo cp "$STATE_ROOT/install-snapshots/config.env.preinstall" "$STATE_ROOT/config.env"
  sudo chmod 0600 "$STATE_ROOT/config.env"
  printf '[rollback] restored previous config.env snapshot\n'
else
  sudo rm -f "$STATE_ROOT/config.env"
fi
printf '[rollback] complete; credential store and workspaces were not deleted\n'
