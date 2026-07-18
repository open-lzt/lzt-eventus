#!/usr/bin/env bash
# Load recorded catalog pages offline (no live API) for dev/CI.
# Feeds a JSON envelope of lots through the engine's offline seed hook.
set -euo pipefail
source "$(dirname "$0")/_lib.sh"
cd "$LZT_REPO_ROOT"

usage() { cat <<'EOF'
seed.sh — replay recorded catalog pages into the event log (offline, no live API).

Usage: scripts/seed.sh [--file <path>] [--help]
  --file   JSON envelope of recorded lots
           (default: tests/fixtures/catalog_seed.json)
EOF
}
FILE="$LZT_REPO_ROOT/tests/fixtures/catalog_seed.json"
while [ $# -gt 0 ]; do case "$1" in
  --help) usage; exit 0 ;;
  --file) FILE="${2:?--file needs a value}"; shift 2 ;;
  *) die "unknown arg: $1 (see --help)" ;;
esac; done

[ -f "$FILE" ] || die "fixture not found: $FILE"
need uv
load_env

phase "Seeding from $FILE"
uvrun python -m event_engine seed --file "$FILE"
ok "seed loaded"
