#!/usr/bin/env bash
# Serve the homi site on the tailnet only (bound to this device's Tailscale IP).
# Usage:  docs/site/serve.sh [port]      (default 7420)   ·   Ctrl-C to stop.
set -euo pipefail
PORT="${1:-7420}"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IP="$(tailscale ip -4 2>/dev/null | head -1)"
[ -n "$IP" ] || { echo "no tailscale IP (is tailscale up?)"; exit 1; }
HOST="$(tailscale status --json 2>/dev/null | python3 -c 'import json,sys;print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))' 2>/dev/null || echo "$IP")"
pkill -f "http.server $PORT" 2>/dev/null || true
echo "homi site → http://$HOST:$PORT/   (also http://$IP:$PORT/)"
echo "tailnet-only · Ctrl-C to stop"
exec python3 -m http.server "$PORT" --bind "$IP" --directory "$DIR"
