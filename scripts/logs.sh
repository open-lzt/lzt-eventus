#!/usr/bin/env bash
# Tail daemon logs (compose or systemd journal).
set -euo pipefail
source "$(dirname "$0")/_lib.sh"
cd "$LZT_REPO_ROOT"

usage() { cat <<'EOF'
logs.sh — show engine daemon logs.

Usage: scripts/logs.sh [--follow] [--tail N] [--systemd] [--help]
  --follow / -f   stream new log lines
  --tail N        last N lines (default 200)
  --systemd       read journalctl for lzt-core.service instead of compose
EOF
}
FOLLOW=0; TAIL=200; MODE="compose"
while [ $# -gt 0 ]; do case "$1" in
  --help) usage; exit 0 ;;
  --follow|-f) FOLLOW=1; shift ;;
  --tail) TAIL="${2:?--tail needs a value}"; shift 2 ;;
  --systemd) MODE="systemd"; shift ;;
  *) die "unknown arg: $1 (see --help)" ;;
esac; done

load_env
if [ "$MODE" = "systemd" ]; then
  need journalctl
  if [ "$FOLLOW" = 1 ]; then
    exec journalctl -u lzt-core.service -n "$TAIL" -f
  else
    exec journalctl -u lzt-core.service -n "$TAIL" --no-pager
  fi
fi

if [ "$FOLLOW" = 1 ]; then
  compose logs --tail "$TAIL" -f engine
else
  compose logs --tail "$TAIL" engine
fi
