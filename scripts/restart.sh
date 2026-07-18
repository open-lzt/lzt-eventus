#!/usr/bin/env bash
# Restart the daemon: stop, start, then wait on the health gate.
set -euo pipefail
source "$(dirname "$0")/_lib.sh"
cd "$LZT_REPO_ROOT"

usage() { cat <<'EOF'
restart.sh — stop then start the engine, gated on /healthz + /readyz.

Usage: scripts/restart.sh [--systemd] [--build] [--help]
  --systemd   restart via systemctl (lzt-core.service)
  --build     rebuild the engine image before starting (compose mode)
EOF
}
MODE="compose"; BUILD=0
for a in "$@"; do case "$a" in
  --help) usage; exit 0 ;;
  --systemd) MODE="systemd" ;;
  --build) BUILD=1 ;;
  *) die "unknown arg: $a (see --help)" ;;
esac; done

need uv
load_env

if [ "$MODE" = "systemd" ]; then
  need systemctl
  phase "Restarting lzt-core.service"
  sudo systemctl restart lzt-core.service
else
  phase "Restarting engine container"
  compose stop engine
  if [ "$BUILD" = 1 ]; then compose up -d --build engine; else compose up -d engine; fi
fi

phase "Health gate"
if uvrun python "$LZT_REPO_ROOT/scripts/health.py" --retries 30 --interval 2; then
  ok "engine healthy"
else
  die "engine did not pass health after restart — see scripts/logs.sh --follow"
fi
