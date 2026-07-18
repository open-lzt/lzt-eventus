#!/usr/bin/env bash
# Show daemon + Postgres + Redis health and the last-deployed commit.
set -euo pipefail
source "$(dirname "$0")/_lib.sh"
cd "$LZT_REPO_ROOT"

usage() { cat <<'EOF'
status.sh — report engine + store health and the deployed revision.

Usage: scripts/status.sh [--help]
EOF
}
[ "${1:-}" = "--help" ] && { usage; exit 0; }

load_env

phase "Deployed revision"
if command -v git >/dev/null 2>&1 && git rev-parse --git-dir >/dev/null 2>&1; then
  info "HEAD:     $(git rev-parse --short HEAD) ($(git log -1 --format='%cr' 2>/dev/null))"
  info "branch:   $(git rev-parse --abbrev-ref HEAD)"
else
  info "not a git checkout"
fi
[ -f "$LZT_REPO_ROOT/.last_deploy_sha.bak" ] && info "rollback target: $(cut -c1-12 "$LZT_REPO_ROOT/.last_deploy_sha.bak")"

phase "Containers"
compose ps 2>/dev/null || warn "compose not available / stack down"

phase "Store health"
if compose exec -T postgres pg_isready -U "${POSTGRES_USER:-lzt}" >/dev/null 2>&1; then
  ok "Postgres: accepting connections"
else
  warn "Postgres: not ready"
fi
if compose exec -T redis redis-cli ping 2>/dev/null | grep -qi pong; then
  ok "Redis: PONG"
else
  warn "Redis: no PONG"
fi

phase "Daemon health"
if uvrun python "$LZT_REPO_ROOT/scripts/health.py" --retries 1; then
  ok "engine: healthy ($(health_base))"
else
  warn "engine: unhealthy or down"
fi
