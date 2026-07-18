#!/usr/bin/env bash
# Revert the last update: code -> previous SHA, DB -> one migration down, restart.
# Without --to, uses .last_deploy_sha.bak written by update.sh.
set -euo pipefail
source "$(dirname "$0")/_lib.sh"
cd "$LZT_REPO_ROOT"

usage() { cat <<'EOF'
rollback.sh — revert the last update (code + one migration step + restart).

Usage: scripts/rollback.sh [--to <sha>] [--no-migrate] [--help]
  --to           explicit SHA to roll back to (default: .last_deploy_sha.bak)
  --no-migrate   skip the alembic downgrade -1 step (code-only rollback)
EOF
}
TO=""; MIGRATE=1
while [ $# -gt 0 ]; do case "$1" in
  --help) usage; exit 0 ;;
  --to) TO="${2:?--to needs a value}"; shift 2 ;;
  --no-migrate) MIGRATE=0; shift ;;
  *) die "unknown arg: $1 (see --help)" ;;
esac; done

need git; need uv
load_env

if [ -z "$TO" ]; then
  [ -f .last_deploy_sha.bak ] || die "no --to and no .last_deploy_sha.bak — cannot infer rollback target"
  TO="$(cat .last_deploy_sha.bak)"
fi
git cat-file -e "$TO^{commit}" 2>/dev/null || die "unknown commit: $TO"

phase "Reverting code to $TO"
git reset --hard "$TO"
ok "code at $TO"

phase "Syncing deps"
uv sync --extra engine

phase "Rebuilding the engine image"
compose build engine

if [ "$MIGRATE" = 1 ]; then
  phase "Downgrading one migration step"
  alembic downgrade -1 || warn "alembic downgrade -1 failed/no-op — verify schema manually"
fi

phase "Restarting daemon"
compose up -d engine
if uvrun python "$LZT_REPO_ROOT/scripts/health.py" --retries 30 --interval 2; then
  ok "rollback complete — engine healthy at $TO"
else
  die "engine still unhealthy after rollback to $TO — manual intervention required"
fi
