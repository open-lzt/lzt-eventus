#!/usr/bin/env bash
# Gracefully stop the daemon (SIGTERM, compose stops the app only — stores stay up).
# Supports a systemd run mode too (--systemd).
set -euo pipefail
source "$(dirname "$0")/_lib.sh"
cd "$LZT_REPO_ROOT"

usage() { cat <<'EOF'
stop.sh — gracefully stop the engine daemon (stores left running).

Usage: scripts/stop.sh [--systemd] [--all] [--help]
  --systemd   stop via systemctl (lzt-core.service) instead of compose
  --all       also stop Postgres + Redis (compose stop, data volume kept)
EOF
}
MODE="compose"; ALL=0
for a in "$@"; do case "$a" in
  --help) usage; exit 0 ;;
  --systemd) MODE="systemd" ;;
  --all) ALL=1 ;;
  *) die "unknown arg: $a (see --help)" ;;
esac; done

load_env
if [ "$MODE" = "systemd" ]; then
  need systemctl
  phase "Stopping lzt-core.service (graceful SIGTERM)"
  sudo systemctl stop lzt-core.service
  ok "service stopped"
  exit 0
fi

# compose stop sends SIGTERM and waits for graceful shutdown (stop_grace_period).
phase "Stopping engine container (graceful)"
compose stop engine
ok "engine stopped"
if [ "$ALL" = 1 ]; then
  phase "Stopping stores"
  compose stop postgres redis
  ok "stores stopped (data volume retained)"
fi
