#!/usr/bin/env bash
# Manage named FOXWORX operator accounts (stored hashed in the local state DB).
# Usage: bash scripts/operator.sh add|passwd|remove|logout <name> | list
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
[[ -x .venv/bin/python ]] || { echo 'Run bash scripts/install.sh first.' >&2; exit 1; }
exec .venv/bin/python -m foxworx.cli operator "$@"
