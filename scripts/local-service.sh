#!/usr/bin/env bash
# Point the existing deployment at the loopback 127.0.0.1:8765 listener.
# Login stays required; this never enables open (no-login) mode.
# Does not restart an active service automatically.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
[[ -x "$ROOT/.venv/bin/python" ]] || { echo 'Install this release before switching to local-only mode.' >&2; exit 1; }
"$ROOT/.venv/bin/python" -m foxworx.cli configure --local-service
printf 'To apply the change: systemctl --user restart foxworx-workbench\n'
