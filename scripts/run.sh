#!/usr/bin/env bash
# Run lzt-core entrypoints. Usage:
#   scripts/run.sh demo-list <category> [--limit N]   # wave-01 SDK demo
#   scripts/run.sh engine [--dry-run]                 # wave-02 event daemon
set -euo pipefail
cd "$(dirname "$0")/.."

PY="${PYTHON:-.venv/Scripts/python.exe}"
[ -x "$PY" ] || PY="python"

cmd="${1:-}"
shift || true

case "$cmd" in
  demo-list)
    exec "$PY" -m lzt_core demo-list "$@"
    ;;
  engine)
    exec "$PY" -m event_engine run "$@"
    ;;
  *)
    echo "usage: run.sh {demo-list <category> [--limit N] | engine [--dry-run]}" >&2
    exit 64
    ;;
esac
