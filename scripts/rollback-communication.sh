#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="${MATTERMOST_INSTALL_DIR:-/opt/ffc-ai-mattermost}"
DESTRUCTIVE=false

while [ "$#" -gt 0 ]; do
  case "$1" in
    --delete-volumes) DESTRUCTIVE=true ;;
    -h|--help) printf 'usage: %s [--delete-volumes]\n' "$0"; exit 0 ;;
    *) exit 2 ;;
  esac
  shift
done

if [ -f "$INSTALL_DIR/docker-compose.yml" ]; then
  (cd "$INSTALL_DIR" && sudo docker compose down)
fi

if [ "$DESTRUCTIVE" = true ]; then
  printf '[rollback-communication] deleting %s after explicit --delete-volumes\n' "$INSTALL_DIR"
  sudo rm -rf "$INSTALL_DIR"
else
  printf '[rollback-communication] containers stopped; data retained at %s\n' "$INSTALL_DIR"
fi
