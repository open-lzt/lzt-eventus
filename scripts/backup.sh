#!/usr/bin/env bash
# pg_dump the self-hosted Postgres event log to a round-trippable archive.
# Runs pg_dump inside the postgres container (right client version, local access).
set -euo pipefail
source "$(dirname "$0")/_lib.sh"
cd "$LZT_REPO_ROOT"

usage() { cat <<'EOF'
backup.sh — dump the Postgres event log to a custom-format archive (pg_restore-able).

Usage: scripts/backup.sh [--out <file>] [--help]
  --out   archive path (default: backups/lzt-core-<UTC-timestamp>.dump)
DB target: POSTGRES_USER/POSTGRES_DB (.env) or lzt/lzt_core defaults.
EOF
}
OUT=""
while [ $# -gt 0 ]; do case "$1" in
  --help) usage; exit 0 ;;
  --out) OUT="${2:?--out needs a value}"; shift 2 ;;
  *) die "unknown arg: $1 (see --help)" ;;
esac; done

load_env
PGUSER="${POSTGRES_USER:-lzt}"
PGDB="${POSTGRES_DB:-lzt_core}"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="${OUT:-$LZT_REPO_ROOT/backups/lzt-core-$TS.dump}"
mkdir -p "$(dirname "$OUT")"

phase "Dumping $PGDB (user $PGUSER) -> $OUT"
# -Fc custom format = compressed + selective pg_restore. -T to stream binary cleanly.
compose exec -T postgres pg_dump -U "$PGUSER" -d "$PGDB" -Fc > "$OUT"
SIZE="$(wc -c < "$OUT" | tr -d ' ')"
[ "$SIZE" -gt 0 ] || die "backup is empty — dump failed"
ok "backup written: $OUT ($SIZE bytes)"
