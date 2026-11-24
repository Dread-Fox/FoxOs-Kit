#!/usr/bin/env bash
# Run FOXWORX without systemd (containers, Alpine/OpenRC, Void/runit, Devuan, Artix...).
#   bash scripts/serve.sh start | stop | status | restart
set -uo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="${FOXWORX_CONFIG:-$HOME/.config/foxworx-workbench/settings.env}"
STATE="$HOME/.local/state/foxworx"; PIDF="$STATE/foxworx.pid"; LOG="$STATE/foxworx.log"
mkdir -p "$STATE"
port() { sed -n 's/^FOXWORX_PORT="\{0,1\}\([0-9]*\)"\{0,1\}$/\1/p' "$CONFIG" 2>/dev/null | tail -1; }
running() { [ -f "$PIDF" ] && kill -0 "$(cat "$PIDF")" 2>/dev/null; }
up() { curl -s -o /dev/null --max-time 2 "http://127.0.0.1:$(port || echo 8765)/api/auth-mode"; }
case "${1:-status}" in
  start)
    running && { echo "FOXWORX already running (pid $(cat "$PIDF"))"; exit 0; }
    cd "$ROOT" && setsid "$ROOT/.venv/bin/python" -m foxworx.cli serve --config "$CONFIG" >> "$LOG" 2>&1 < /dev/null &
    echo $! > "$PIDF"
    for _ in $(seq 1 40); do up && { echo "FOXWORX running: http://127.0.0.1:$(port || echo 8765) (pid $(cat "$PIDF"))"; exit 0; }
      running || break; sleep 0.5; done
    echo "FOXWORX did not start; last log lines:"; tail -n 15 "$LOG"; exit 1 ;;
  stop)
    running && kill "$(cat "$PIDF")" && echo "FOXWORX stopped"; rm -f "$PIDF" ;;
  restart) "$0" stop; sleep 1; exec "$0" start ;;
  status) if running; then echo "running (pid $(cat "$PIDF"))"; else echo "not running"; exit 3; fi ;;
  *) echo "usage: bash scripts/serve.sh start|stop|status|restart"; exit 2 ;;
esac
