#!/usr/bin/env bash
set -euo pipefail

SYSTEM_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

export HOME="$SYSTEM_DIR/.home"
export PYTHONPYCACHEPREFIX="$SYSTEM_DIR/.pycache"

mkdir -p "$HOME" "$PYTHONPYCACHEPREFIX"

exec "$SYSTEM_DIR/.venv/bin/python" "$@"
