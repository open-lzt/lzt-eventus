#!/usr/bin/env bash
# Retention: drop event-log partitions below the retention watermark.
set -euo pipefail
source "$(dirname "$0")/_lib.sh"
cd "$LZT_REPO_ROOT"

usage() { cat <<'EOF'
prune.sh — apply retention: drop event-log partitions below the watermark.

Usage: scripts/prune.sh [--dry-run] [--help]
  --dry-run   report which partitions WOULD be dropped, drop nothing
Watermark: LZT_RETENTION_MONTHS (EngineConfig, default 3).
EOF
}
DRY=0
for a in "$@"; do case "$a" in
  --help) usage; exit 0 ;;
  --dry-run) DRY=1 ;;
  *) die "unknown arg: $a (see --help)" ;;
esac; done

need uv
load_env

phase "Pruning event log below retention watermark"
info "retention: ${LZT_RETENTION_MONTHS:-3} month(s)"
if [ "$DRY" = 1 ]; then
  uvrun python -m event_engine prune --dry-run
  ok "dry-run complete (nothing dropped)"
else
  uvrun python -m event_engine prune
  ok "prune complete"
fi
