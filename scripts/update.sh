#!/usr/bin/env bash
# Rolling update: git pull -> uv sync -> E2E GATE -> migrate -> restart -> health gate.
# The E2E gate runs the hermetic `pytest -m e2e` suite against the NEW code BEFORE
# anything touches the DB or the live daemon. If a single e2e test fails, the update
# is aborted: the checkout is reverted to the previous SHA and the running service is
# never rebuilt — so a bad commit can never be rolled out. On *health* failure (after
# restart) it still auto-rolls-back via rollback.sh (revert code + downgrade + restart).
set -euo pipefail
source "$(dirname "$0")/_lib.sh"
cd "$LZT_REPO_ROOT"

usage() { cat <<'EOF'
update.sh — rolling update with an e2e pre-swap gate, a health gate, and auto-rollback.

Usage: scripts/update.sh [--ref <branch|tag>] [--no-rollback] [--skip-e2e] [--dry-run] [--help]
  --ref          git ref to update to (default: current branch's upstream)
  --no-rollback  do NOT auto-rollback if the health gate fails
  --skip-e2e     skip the pre-swap e2e gate (NOT recommended; autoupdate sets this
                 from e2e_gate in deploy/autoupdate.yml)
  --dry-run      show what would change (git fetch + diff), make no changes
EOF
}
REF=""; ROLLBACK=1; DRY=0; E2E=1
while [ $# -gt 0 ]; do case "$1" in
  --help) usage; exit 0 ;;
  --ref) REF="${2:?--ref needs a value}"; shift 2 ;;
  --no-rollback) ROLLBACK=0; shift ;;
  --skip-e2e) E2E=0; shift ;;
  --dry-run) DRY=1; shift ;;
  *) die "unknown arg: $1 (see --help)" ;;
esac; done

need git; need uv
load_env

PREV_SHA="$(git rev-parse HEAD)"
echo "$PREV_SHA" > "$LZT_REPO_ROOT/.last_deploy_sha.bak"

phase "Fetching"
git fetch --tags --prune origin
TARGET="${REF:-@{u}}"
NEW_SHA="$(git rev-parse "$TARGET")"
if [ "$PREV_SHA" = "$NEW_SHA" ]; then
  ok "already up to date ($PREV_SHA) — nothing to do"
  exit 0
fi
info "current:  $PREV_SHA"
info "target:   $NEW_SHA ($TARGET)"

if [ "$DRY" = 1 ]; then
  git --no-pager log --oneline "$PREV_SHA..$NEW_SHA" | sed 's/^/  /'
  ok "dry-run: no changes applied"
  exit 0
fi

phase "Applying code ($TARGET)"
git merge --ff-only "$NEW_SHA" 2>/dev/null || git reset --hard "$NEW_SHA"
ok "checked out $NEW_SHA"

phase "Syncing deps"
SYNC_EXTRAS=(--extra engine)
[ "$E2E" = 1 ] && SYNC_EXTRAS+=(--extra dev)  # the gate needs pytest
uv sync "${SYNC_EXTRAS[@]}"

if [ "$E2E" = 1 ]; then
  phase "E2E gate (pre-swap)"
  # Hermetic suite (Memory stores, no Postgres/network) over the NEW code. One
  # failure aborts BEFORE migrate/restart — the live daemon is never rebuilt.
  if uvrun python -m pytest -m e2e -q; then
    ok "e2e gate passed — proceeding with rollout"
  else
    warn "e2e gate FAILED — aborting update; reverting to $PREV_SHA"
    git reset --hard "$PREV_SHA"
    uv sync --extra engine >/dev/null 2>&1 || true
    die "update aborted by e2e gate — live service untouched, still on $PREV_SHA"
  fi
fi

phase "Building the engine image"
# Before migrating: migrate.sh runs alembic *inside* this image in compose mode,
# so it must already contain the new migration files, not the previous release's.
compose build engine

phase "Migrating"
bash "$LZT_REPO_ROOT/scripts/migrate.sh"

phase "Restarting daemon"
compose up -d engine

phase "Health gate"
if uvrun python "$LZT_REPO_ROOT/scripts/health.py" --retries 30 --interval 2; then
  ok "update OK — engine healthy at $NEW_SHA"
  exit 0
fi

warn "health gate FAILED after update to $NEW_SHA"
if [ "$ROLLBACK" = 1 ]; then
  warn "auto-rolling back to $PREV_SHA"
  bash "$LZT_REPO_ROOT/scripts/rollback.sh" --to "$PREV_SHA"
  die "update rolled back — investigate before retrying"
fi
die "update unhealthy and --no-rollback set — manual intervention required"
