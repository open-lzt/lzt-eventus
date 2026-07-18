#!/usr/bin/env bash
# Apply database migrations to head. Idempotent: re-running on an up-to-date DB
# is a no-op (alembic compares against the version table).
set -euo pipefail
source "$(dirname "$0")/_lib.sh"
cd "$LZT_REPO_ROOT"

usage() { cat <<'EOF'
migrate.sh — run Alembic migrations up to head (idempotent).

Usage: scripts/migrate.sh [--help]
Env:   LZT_DATABASE_URL (else EngineConfig default)
EOF
}
[ "${1:-}" = "--help" ] && { usage; exit 0; }

load_env
phase "Alembic upgrade head"
alembic upgrade head
ok "database at head"
