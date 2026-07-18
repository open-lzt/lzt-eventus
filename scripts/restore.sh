#!/usr/bin/env bash
# pg_restore a backup.sh archive into the self-hosted Postgres event log.
# Round-trips with backup.sh. DESTRUCTIVE: --clean drops existing objects first.
set -euo pipefail
source "$(dirname "$0")/_lib.sh"
cd "$LZT_REPO_ROOT"

usage() { cat <<'EOF'
restore.sh — restore a pg_dump archive into the Postgres event log.

Usage: scripts/restore.sh --file <archive> [--yes] [--help]
  --file   archive produced by backup.sh (custom format)
  --yes    skip the destructive-restore confirmation prompt
WARNING: restores with --clean (drops existing objects). Take a backup first.
EOF
}
FILE=""; ASSUME_YES=0
while [ $# -gt 0 ]; do case "$1" in
  --help) usage; exit 0 ;;
  --file) FILE="${2:?--file needs a value}"; shift 2 ;;
  --yes) ASSUME_YES=1; shift ;;
  *) die "unknown arg: $1 (see --help)" ;;
esac; done

[ -n "$FILE" ] || die "--file is required (see --help)"
[ -f "$FILE" ] || die "archive not found: $FILE"
load_env
PGUSER="${POSTGRES_USER:-lzt}"
PGDB="${POSTGRES_DB:-lzt_core}"

if [ "$ASSUME_YES" != 1 ]; then
  warn "This will OVERWRITE database '$PGDB' from $FILE."
  printf 'Type the database name to confirm: '
  read -r reply
  [ "$reply" = "$PGDB" ] || die "confirmation mismatch — aborted"
fi

phase "Restoring $FILE -> $PGDB (user $PGUSER)"
# --clean --if-exists makes restore idempotent; -1 wraps it in one transaction.
compose exec -T postgres pg_restore -U "$PGUSER" -d "$PGDB" --clean --if-exists -1 < "$FILE"
ok "restore complete into $PGDB"
