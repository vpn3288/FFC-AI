#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="${MATTERMOST_INSTALL_DIR:-/opt/ffc-ai-mattermost}"
ADMIN_EMAIL="${MATTERMOST_ADMIN_EMAIL:-admin@example.invalid}"
ADMIN_USERNAME="${MATTERMOST_ADMIN_USERNAME:-ai-admin}"
ADMIN_PASSWORD="${MATTERMOST_ADMIN_PASSWORD:-}"

usage() {
  printf 'usage: MATTERMOST_ADMIN_PASSWORD=... %s [--email EMAIL] [--username USERNAME]\n' "$0"
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --email) ADMIN_EMAIL="$2"; shift ;;
    --username) ADMIN_USERNAME="$2"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage; exit 2 ;;
  esac
  shift
done

[ -n "$ADMIN_PASSWORD" ] || { usage; exit 2; }

mmctl() {
  if [ -x "$INSTALL_DIR/mattermost/bin/mmctl" ]; then
    (cd "$INSTALL_DIR/mattermost" && bin/mmctl --local "$@")
  else
    (cd "$INSTALL_DIR" && compose exec -T mattermost mmctl --local "$@")
  fi
}

compose() {
  if sudo docker compose version >/dev/null 2>&1; then
    sudo docker compose "$@"
  else
    sudo docker-compose "$@"
  fi
}

if ! mmctl user search "$ADMIN_USERNAME" >/dev/null 2>&1; then
  mmctl user create --email "$ADMIN_EMAIL" --username "$ADMIN_USERNAME" --password "$ADMIN_PASSWORD" --system-admin --email-verified --disable-welcome-email
else
  mmctl user change-password "$ADMIN_USERNAME" --password "$ADMIN_PASSWORD" >/dev/null
  mmctl user email "$ADMIN_USERNAME" "$ADMIN_EMAIL" >/dev/null 2>&1 || true
fi
mmctl roles system-admin "$ADMIN_USERNAME" >/dev/null

cat <<EOF
[mattermost-first-admin] system admin ready: $ADMIN_USERNAME
[mattermost-first-admin] Log into Mattermost as this admin and create a personal access token.
[mattermost-first-admin] Export MATTERMOST_ADMIN_TOKEN with that token before running bootstrap-mattermost.sh.
EOF
