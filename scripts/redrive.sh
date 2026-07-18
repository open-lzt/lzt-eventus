#!/usr/bin/env bash
# Re-inject parked dead_letter events for a consumer after a fix.
set -euo pipefail
source "$(dirname "$0")/_lib.sh"
cd "$LZT_REPO_ROOT"

usage() { cat <<'EOF'
redrive.sh — re-deliver a consumer's dead-lettered events after a fix.

Usage: scripts/redrive.sh --consumer <name> [--limit N] [--help]
  --consumer   consumer whose DLQ to drain back into delivery
  --limit      max events to redrive this run (default: all)
EOF
}
CONSUMER=""; LIMIT=""
while [ $# -gt 0 ]; do case "$1" in
  --help) usage; exit 0 ;;
  --consumer) CONSUMER="${2:?--consumer needs a value}"; shift 2 ;;
  --limit) LIMIT="${2:?--limit needs a value}"; shift 2 ;;
  *) die "unknown arg: $1 (see --help)" ;;
esac; done

[ -n "$CONSUMER" ] || die "--consumer is required (see --help)"
need uv
load_env

phase "Redriving DLQ: $CONSUMER"
if [ -n "$LIMIT" ]; then
  uvrun python -m event_engine redrive --consumer "$CONSUMER" --limit "$LIMIT"
else
  uvrun python -m event_engine redrive --consumer "$CONSUMER"
fi
ok "redrive complete for $CONSUMER"
