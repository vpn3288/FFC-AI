#!/usr/bin/env bash
set -euo pipefail

DRY_RUN=false
VPS_HOST=""
VPS_USER="root"
SSH_KEY="/root/.ssh/ffc_ai_runner_tunnel"
REMOTE_BIND="127.0.0.1"
REMOTE_PORT="18765"
LOCAL_HOST="127.0.0.1"
LOCAL_PORT="8765"
SERVICE_NAME="ai-remote-runner-tunnel"

usage() {
  printf 'usage: %s --vps-host HOST [--vps-user USER] [--ssh-key PATH] [--remote-port PORT] [--local-port PORT] [--dry-run]\n' "$0"
}

log() {
  printf '[setup-runner-tunnel] %s\n' "$*"
}

run() {
  if [ "$DRY_RUN" = true ]; then
    printf '[dry-run] %s\n' "$*"
  else
    "$@"
  fi
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=true ;;
    --vps-host) VPS_HOST="$2"; shift ;;
    --vps-user) VPS_USER="$2"; shift ;;
    --ssh-key) SSH_KEY="$2"; shift ;;
    --remote-bind) REMOTE_BIND="$2"; shift ;;
    --remote-port) REMOTE_PORT="$2"; shift ;;
    --local-host) LOCAL_HOST="$2"; shift ;;
    --local-port) LOCAL_PORT="$2"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage; exit 2 ;;
  esac
  shift
done

[ -n "$VPS_HOST" ] || { usage; exit 2; }
case "$REMOTE_PORT:$LOCAL_PORT" in
  *[!0-9:]*|*:|:*) log 'ports must be numeric'; exit 2 ;;
esac

KEY_DIR="$(dirname "$SSH_KEY")"
run mkdir -p "$KEY_DIR"
run chmod 0700 "$KEY_DIR"
if [ ! -f "$SSH_KEY" ]; then
  log "generating SSH key at $SSH_KEY"
  run ssh-keygen -t ed25519 -N '' -f "$SSH_KEY" -C "ffc-ai-runner-tunnel@$VPS_HOST"
fi
run chmod 0600 "$SSH_KEY"

if [ "$DRY_RUN" = false ]; then
  if ! ssh -i "$SSH_KEY" -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 "$VPS_USER@$VPS_HOST" true; then
    log "SSH key is not authorized on $VPS_USER@$VPS_HOST"
    log "install this public key on the VPS, then rerun:"
    sed 's/^/[setup-runner-tunnel]   /' "$SSH_KEY.pub"
    exit 1
  fi
fi

SERVICE_PATH="/etc/systemd/system/$SERVICE_NAME.service"
if [ "$DRY_RUN" = false ]; then
  cat > "$SERVICE_PATH" <<EOF
[Unit]
Description=AI Remote Runner reverse tunnel to communication VPS
After=network-online.target ai-remote-runner.service
Wants=network-online.target
Requires=ai-remote-runner.service

[Service]
Type=simple
ExecStart=/usr/bin/ssh -i $SSH_KEY -N -o BatchMode=yes -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -o StrictHostKeyChecking=accept-new -R $REMOTE_BIND:$REMOTE_PORT:$LOCAL_HOST:$LOCAL_PORT $VPS_USER@$VPS_HOST
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable --now "$SERVICE_NAME.service"
  systemctl is-active "$SERVICE_NAME.service" >/dev/null
else
  log "would write $SERVICE_PATH and start $SERVICE_NAME.service"
fi

log "bridge command URL from the VPS is http://$REMOTE_BIND:$REMOTE_PORT/bridge/command"
