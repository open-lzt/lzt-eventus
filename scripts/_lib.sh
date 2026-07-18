#!/usr/bin/env bash
# Shared helpers for lzt-core ops scripts: colour output, phased progress,
# .env loading, dependency checks. Source it, don't execute it:
#   source "$(dirname "$0")/_lib.sh"
# Idempotent and side-effect-free on source (only defines functions + repo root).
set -euo pipefail

# Repo root = parent of scripts/. Resolved once; every script cd's here.
LZT_REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  C_RESET=$'\033[0m'; C_RED=$'\033[31m'; C_GREEN=$'\033[32m'
  C_YELLOW=$'\033[33m'; C_BLUE=$'\033[34m'; C_DIM=$'\033[2m'; C_BOLD=$'\033[1m'
else
  C_RESET=""; C_RED=""; C_GREEN=""; C_YELLOW=""; C_BLUE=""; C_DIM=""; C_BOLD=""
fi

_phase=0
phase()  { _phase=$((_phase + 1)); printf '%s[%d] %s%s\n' "$C_BOLD$C_BLUE" "$_phase" "$*" "$C_RESET"; }
info()   { printf '%s•%s %s\n' "$C_DIM" "$C_RESET" "$*"; }
ok()     { printf '%s✓%s %s\n' "$C_GREEN" "$C_RESET" "$*"; }
warn()   { printf '%s!%s %s\n' "$C_YELLOW" "$C_RESET" "$*" >&2; }
die()    { printf '%s✗ %s%s\n' "$C_RED" "$*" "$C_RESET" >&2; exit 1; }

# need <binary> [hint] — fail loud if a required tool is missing.
need() {
  command -v "$1" >/dev/null 2>&1 || die "missing dependency: $1${2:+ — $2}"
}

# load_env — export every KEY=VALUE from .env into the environment (if present).
# Real env always wins: only sets vars not already exported. Skips comments/blanks.
load_env() {
  local env_file="${1:-$LZT_REPO_ROOT/.env}"
  [ -f "$env_file" ] || { info "no $env_file (using process env / defaults)"; return 0; }
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in ''|'#'*) continue ;; esac
    local key="${line%%=*}"
    [ -n "${!key:-}" ] && continue
    export "${line?}"
  done < "$env_file"
  info "loaded $env_file"
}

# pg_url / cli helpers — read the daemon's stores from env with the same defaults
# as EngineConfig, so ops scripts and the daemon agree without a second source.
db_url()    { printf '%s' "${LZT_DATABASE_URL:-postgresql://lzt:lzt@localhost:5432/lzt_core}"; }
redis_url() { printf '%s' "${LZT_REDIS_URL:-redis://localhost:6379/0}"; }
health_base() { printf 'http://%s:%s' "${LZT_HEALTH_HOST:-127.0.0.1}" "${LZT_HEALTH_PORT:-27543}"; }

# uv passthrough — prefer uv (the project's manager); the deploy host always has it.
uvrun() { uv run "$@"; }

# port_in_use <port> — true if something on 127.0.0.1 already accepts on it.
# Shared boxes run other projects; never assume a "default" host port is free.
port_in_use() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }

# compose <args...> — docker compose against the deploy file, env from .env.
COMPOSE_FILE="$LZT_REPO_ROOT/deploy/docker-compose.yml"
compose() {
  if docker compose version >/dev/null 2>&1; then
    docker compose -f "$COMPOSE_FILE" --env-file "$LZT_REPO_ROOT/.env" "$@"
  else
    need docker-compose "install Docker Compose v2"
    docker-compose -f "$COMPOSE_FILE" --env-file "$LZT_REPO_ROOT/.env" "$@"
  fi
}

# alembic <args...> — run alembic against whichever Postgres is actually live.
# Compose mode: inside the already-built engine image, over the compose
# network (postgres:5432) — the host may not even have a published port for
# it, or LZT_DATABASE_URL in .env may point at an unrelated bare-metal
# Postgres entirely. Bare-metal: the host's own uv-managed alembic + DSN.
alembic() {
  if [ -n "$(compose ps -q postgres 2>/dev/null)" ]; then
    compose run --rm --no-deps --entrypoint alembic engine "$@"
  else
    uvrun alembic "$@"
  fi
}
