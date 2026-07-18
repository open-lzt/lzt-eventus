#!/usr/bin/env bash
# Reset a consumer cursor to backfill from a sequence number.
set -euo pipefail
source "$(dirname "$0")/_lib.sh"
cd "$LZT_REPO_ROOT"

usage() { cat <<'EOF'
replay.sh — rewind a consumer's cursor so it re-reads from a seq (backfill).

Usage: scripts/replay.sh --consumer <name> --from-seq <N> [--help]
  --consumer   module/consumer name whose cursor to reset
  --from-seq   sequence number to resume from (>= 0)
EOF
}
CONSUMER=""; FROM_SEQ=""
while [ $# -gt 0 ]; do case "$1" in
  --help) usage; exit 0 ;;
  --consumer) CONSUMER="${2:?--consumer needs a value}"; shift 2 ;;
  --from-seq) FROM_SEQ="${2:?--from-seq needs a value}"; shift 2 ;;
  *) die "unknown arg: $1 (see --help)" ;;
esac; done

[ -n "$CONSUMER" ] || die "--consumer is required (see --help)"
[ -n "$FROM_SEQ" ] || die "--from-seq is required (see --help)"
case "$FROM_SEQ" in ''|*[!0-9]*) die "--from-seq must be a non-negative integer" ;; esac
need uv
load_env

phase "Rewinding cursor: $CONSUMER -> seq $FROM_SEQ"
uvrun python -m event_engine replay --consumer "$CONSUMER" --from-seq "$FROM_SEQ"
ok "cursor reset — $CONSUMER will backfill from seq $FROM_SEQ"
