#!/usr/bin/env bash
# One-shot bootstrap: clean host -> running daemon. Idempotent (re-run safe).
#   1. check deps (docker, uv)         4. run migrations
#   2. .env.example -> .env (if absent) 5. start the daemon (compose)
#   3. bring up Postgres + Redis        6. optional auto-updater (per autoupdate.yml)
set -euo pipefail
source "$(dirname "$0")/_lib.sh"
cd "$LZT_REPO_ROOT"

usage() { cat <<'EOF'
install.sh — bootstrap a clean host into a running lzt-core daemon.

Usage: scripts/install.sh [--no-start] [--help]
  --no-start   provision stores + migrate, but don't start the engine
Idempotent: existing .env, running containers, and applied migrations are kept.
After first run, edit .env to add your LZT_TOKENS, then re-run.
EOF
}
NO_START=0
for a in "$@"; do case "$a" in
  --help) usage; exit 0 ;;
  --no-start) NO_START=1 ;;
  *) die "unknown arg: $a (see --help)" ;;
esac; done

phase "Checking dependencies"
if ! command -v docker >/dev/null 2>&1; then
  info "docker not found — installing (get.docker.com)"
  curl -fsSL https://get.docker.com | sh
  ok "docker installed"
else
  info "docker already present"
fi
docker compose version >/dev/null 2>&1 || die "docker present but the compose v2 plugin is missing — see https://docs.docker.com/compose/install/"
if ! command -v uv >/dev/null 2>&1; then
  info "uv not found — installing (astral.sh)"
  curl -fsSL https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
  need uv "installed but not on PATH — open a new shell or 'source \$HOME/.local/bin/env'"
  ok "uv installed"
else
  info "uv already present"
fi
ok "docker + uv present"

phase "Configuration (.env)"
if [ ! -f .env ]; then
  [ -f .env.example ] || die ".env.example missing — cannot seed .env"
  cp .env.example .env
  ok "created .env from .env.example"
  warn "edit .env and set LZT_TOKENS before the engine can poll lzt.market"
else
  info ".env already present (kept)"
fi
load_env

phase "Port check"
# lzt-core ships specific, non-standard default ports for every published
# service (LZT_HEALTH_PORT / LZT_POSTGRES_PORT / LZT_REDIS_PORT, .env.example)
# instead of the well-known 9189/5432/6379-style conventions — a shared box
# running other projects is far less likely to already own an odd 5-digit
# port than a common default. Still warn loudly if a collision happens
# anyway, rather than silently binding the wrong service.
for _pair in "LZT_HEALTH_PORT:health/admin API" "LZT_POSTGRES_PORT:Postgres (compose-published)" "LZT_REDIS_PORT:Redis (compose-published)"; do
  _var="${_pair%%:*}"; _label="${_pair#*:}"
  _port="${!_var:-}"
  [ -n "$_port" ] || continue
  if port_in_use "$_port"; then
    warn "$_var=$_port ($_label) is already in use on this host — pick a different value in .env and re-run"
  fi
done

# lolz.market API token gate — the daemon cannot poll without at least one token.
if ! grep -qE '^LZT_TOKENS=\[?["'\''a-zA-Z0-9]' .env 2>/dev/null \
   || grep -qE '^LZT_TOKENS=(\[\]|""|\[""\]|.*paste-your-token)' .env 2>/dev/null; then
  warn "LZT_TOKENS is empty/placeholder."
  warn "  1. Get a token: https://lzt.market/account/api"
  warn "  2. Edit .env:   LZT_TOKENS=[\"<your-token>\"]   (JSON array; add more, comma-separated)"
  warn "  3. Re-run scripts/install.sh"
  info "provisioning stores anyway so the box is ready"
fi

phase "Installing Python deps (uv sync)"
uv --version >/dev/null
uv sync --extra engine
ok "dependencies synced"

phase "Bringing up Postgres + Redis"
compose up -d postgres redis
info "waiting for Postgres to accept connections"
for i in $(seq 1 30); do
  if compose exec -T postgres pg_isready -U "${POSTGRES_USER:-lzt}" >/dev/null 2>&1; then
    ok "Postgres ready"; break
  fi
  [ "$i" = 30 ] && die "Postgres did not become ready in 30s"
  sleep 1
done

phase "Building the engine image"
# Explicit, separate from `compose up`/`compose run` — neither rebuilds an
# already-built image on its own, so a source change between runs would
# otherwise silently migrate/run stale code.
compose build engine
ok "engine image built"

phase "Running migrations"
bash "$LZT_REPO_ROOT/scripts/migrate.sh"

if [ "$NO_START" = 1 ]; then
  ok "provisioned (engine not started: --no-start)"
  exit 0
fi

phase "Starting the daemon"
compose up -d engine
info "health gate: $(health_base)/healthz"
if uvrun python "$LZT_REPO_ROOT/scripts/health.py" --retries 30 --interval 2; then
  ok "engine healthy"
else
  warn "engine did not pass health within timeout — check: scripts/logs.sh --follow"
fi

phase "Domain + automatic TLS (optional)"
if [ -n "${LZT_DOMAIN:-}" ] && [ "${LZT_DOMAIN}" != "localhost" ]; then
  bash "$LZT_REPO_ROOT/scripts/setup_tls.sh"
else
  info "LZT_DOMAIN not set in .env — skipping (engine stays loopback-only, see docs/deploy.md)"
fi

phase "Optional auto-updater"
AU="$LZT_REPO_ROOT/deploy/autoupdate.yml"
if [ -f "$AU" ] && grep -qE '^[[:space:]]*enabled:[[:space:]]*true' "$AU"; then
  info "autoupdate.yml enabled — start it with: scripts/autoupdate.sh (or the systemd timer)"
else
  info "auto-update disabled (set enabled: true in deploy/autoupdate.yml to opt in)"
fi

ok "install complete — daemon is up. Manage with: scripts/{status,logs,stop,update}.sh"
