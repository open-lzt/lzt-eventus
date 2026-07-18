#!/usr/bin/env bash
# Lint + type-check + unit tests. The CI floor, runnable locally.
set -euo pipefail
cd "$(dirname "$0")/.."

PY="${PYTHON:-.venv/Scripts/python.exe}"
[ -x "$PY" ] || PY="python"

echo "== ruff =="
"$PY" -m ruff check src tests
"$PY" -m ruff format --check src tests
echo "== mypy --strict =="
"$PY" -m mypy src
echo "== pytest =="
"$PY" -m pytest "$@"
