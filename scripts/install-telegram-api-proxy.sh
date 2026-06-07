#!/usr/bin/env bash
set -euo pipefail

DRY_RUN=false
INSTALL_DIR="${TELEGRAM_API_PROXY_INSTALL_DIR:-/opt/ffc-ai-telegram-api-proxy}"
SERVICE_NAME="${TELEGRAM_API_PROXY_SERVICE_NAME:-telegram-api-proxy}"
LISTEN_HOST="${TELEGRAM_API_PROXY_HOST:-127.0.0.1}"
LISTEN_PORT="${TELEGRAM_API_PROXY_PORT:-18081}"
ALLOWED_CLIENTS="${TELEGRAM_PROXY_ALLOWED_CLIENTS:-}"

usage() {
  printf 'usage: %s [--listen-host HOST] [--listen-port PORT] --allow-client IP_OR_CIDR [--allow-client IP_OR_CIDR] [--dry-run]\n' "$0"
}

log() {
  printf '[install-telegram-api-proxy] %s\n' "$*"
}

run() {
  if [ "$DRY_RUN" = true ]; then
    printf '[dry-run] %s\n' "$*"
  else
    "$@"
  fi
}

add_allowed_client() {
  if [ -z "$ALLOWED_CLIENTS" ]; then
    ALLOWED_CLIENTS="$1"
  else
    ALLOWED_CLIENTS="$ALLOWED_CLIENTS,$1"
  fi
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=true ;;
    --listen-host) LISTEN_HOST="$2"; shift ;;
    --listen-port) LISTEN_PORT="$2"; shift ;;
    --allow-client) add_allowed_client "$2"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage; exit 2 ;;
  esac
  shift
done

case "$LISTEN_PORT" in
  ''|*[!0-9]*) log 'listen port must be numeric'; exit 2 ;;
esac

if [ -z "$ALLOWED_CLIENTS" ]; then
  case "$LISTEN_HOST" in
    127.*|localhost|::1)
      ALLOWED_CLIENTS="127.0.0.1,::1"
      ;;
    *)
      log 'refusing to listen on a non-local address without --allow-client'
      usage
      exit 2
      ;;
  esac
fi

if [ "$(id -u)" != 0 ] && [ "$DRY_RUN" = false ]; then
  exec sudo -E bash "$0" \
    --listen-host "$LISTEN_HOST" \
    --listen-port "$LISTEN_PORT" \
    $(printf '%s' "$ALLOWED_CLIENTS" | awk -F, '{for (i=1; i<=NF; i++) if ($i != "") printf "--allow-client %s ", $i}') \
    ${DRY_RUN:+--dry-run}
elif [ "$(id -u)" != 0 ]; then
  log 'dry-run mode on non-root shell; real install will re-run through sudo'
fi

if command -v apt-get >/dev/null 2>&1; then
  run apt-get update
  run apt-get install -y python3 ca-certificates
fi

run install -d -m 0755 "$INSTALL_DIR"

if [ "$DRY_RUN" = false ]; then
  cat > "$INSTALL_DIR/proxy.py" <<'PY'
#!/usr/bin/env python3
import ipaddress
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib import error, request

UPSTREAM = "https://api.telegram.org"
LISTEN_HOST = os.environ.get("TELEGRAM_PROXY_HOST", "127.0.0.1")
LISTEN_PORT = int(os.environ.get("TELEGRAM_PROXY_PORT", "18081"))
ALLOWED_RAW = os.environ.get("TELEGRAM_PROXY_ALLOWED_CLIENTS", "127.0.0.1,::1")
MAX_BODY_BYTES = int(os.environ.get("TELEGRAM_PROXY_MAX_BODY_BYTES", str(20 * 1024 * 1024)))
HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


def allowed_networks():
    networks = []
    for item in [part.strip() for part in ALLOWED_RAW.split(",") if part.strip()]:
        networks.append(ipaddress.ip_network(item, strict=False))
    return networks


ALLOWED = allowed_networks()


def is_allowed(address):
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return any(ip in network for network in ALLOWED)


class ProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        return

    def do_GET(self):
        self.forward()

    def do_POST(self):
        self.forward()

    def do_HEAD(self):
        self.forward()

    def forward(self):
        if not is_allowed(self.client_address[0]):
            self.send_error(403, "client not allowed")
            return

        body = None
        if self.command in {"POST", "PUT", "PATCH"}:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self.send_error(400, "invalid content length")
                return
            if length > MAX_BODY_BYTES:
                self.send_error(413, "request body too large")
                return
            body = self.rfile.read(length) if length else b""

        headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() not in HOP_BY_HOP and key.lower() != "host"
        }
        upstream_request = request.Request(
            UPSTREAM + self.path,
            data=body,
            headers=headers,
            method=self.command,
        )

        try:
            with request.urlopen(upstream_request, timeout=60) as upstream_response:
                payload = upstream_response.read()
                self.send_response(upstream_response.status)
                for key, value in upstream_response.headers.items():
                    lower = key.lower()
                    if lower in HOP_BY_HOP or lower == "content-length":
                        continue
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(payload)
        except error.HTTPError as exc:
            payload = exc.read()
            self.send_response(exc.code)
            for key, value in exc.headers.items():
                lower = key.lower()
                if lower in HOP_BY_HOP or lower == "content-length":
                    continue
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(payload)
        except Exception:
            payload = b"telegram api proxy error\n"
            self.send_response(502)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(payload)


def main():
    server = ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), ProxyHandler)
    server.serve_forever()


if __name__ == "__main__":
    main()
PY
  chmod 0755 "$INSTALL_DIR/proxy.py"
fi

SERVICE_PATH="/etc/systemd/system/$SERVICE_NAME.service"
if [ "$DRY_RUN" = false ]; then
  cat > "$SERVICE_PATH" <<EOF
[Unit]
Description=FFC-AI Telegram Bot API proxy
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=nobody
Environment=TELEGRAM_PROXY_HOST=$LISTEN_HOST
Environment=TELEGRAM_PROXY_PORT=$LISTEN_PORT
Environment=TELEGRAM_PROXY_ALLOWED_CLIENTS=$ALLOWED_CLIENTS
ExecStart=/usr/bin/python3 $INSTALL_DIR/proxy.py
Restart=always
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=true

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable --now "$SERVICE_NAME.service"
  systemctl is-active "$SERVICE_NAME.service" >/dev/null
else
  log "would write $INSTALL_DIR/proxy.py and $SERVICE_PATH"
fi

log "Telegram API proxy is ready at http://$LISTEN_HOST:$LISTEN_PORT"
log "Pair blocked runners with: scripts/pair-telegram.sh --api-base http://PROXY_MACHINE_IP:$LISTEN_PORT --telegram-id YOUR_TELEGRAM_ID"
