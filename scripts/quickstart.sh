#!/usr/bin/env bash
# One-command interactive bootstrap for the lazy: prompt for the handful of
# things only a human can supply (lzt.market token, domain, contact email),
# generate what can be generated (admin key), write .env, then hand off to
# install.sh for everything scriptable (deps, stores, migrations, TLS, health
# gate). Clone to running prod in one command:
#   git clone https://github.com/open-lzt/lzt-eventus.git lzt-core && cd lzt-core && scripts/quickstart.sh
set -euo pipefail
source "$(dirname "$0")/_lib.sh"
cd "$LZT_REPO_ROOT"

usage() { cat <<'EOF'
quickstart.sh — interactive one-command bootstrap: prompt -> .env -> install.sh.

Usage: scripts/quickstart.sh [--help]

Prompts for: lzt.market API token, public domain (optional), ACME contact
email (only if a domain is given). Generates LZT_ADMIN_API_KEY itself. Runs
exactly once per box — if .env already exists, skips straight to install.sh
(edit .env by hand to change anything after the first run).
EOF
}
case "${1:-}" in --help|-h) usage; exit 0 ;; esac

banner() {
  printf '\n%s%slzt-core%s%s — one command to prod\n' "$C_BOLD" "$C_BLUE" "$C_RESET" "$C_BOLD"
  printf '%s────────────────────────────────────────%s\n\n' "$C_BLUE" "$C_RESET"
}
banner

# print_report — final summary card, read straight off the live .env so it
# always reflects what actually got deployed (never re-derived by hand).
# The admin key is shown here and ONLY here — same "one-time plaintext,
# shown once" convention the management API itself uses for webhook secrets.
print_report() {
  printf '\n%s── Deployed ────────────────────────────────────────%s\n' "$C_BOLD$C_GREEN" "$C_RESET"
  python3 - <<'PY'
import pathlib
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

env = {}
for line in pathlib.Path(".env").read_text().splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    k, _, v = line.partition("=")
    env[k] = v

domain = env.get("LZT_DOMAIN", "").strip()
port = env.get("LZT_HEALTH_PORT", "27543").strip() or "27543"
admin_key = env.get("LZT_ADMIN_API_KEY", "").strip()
docs_enabled = env.get("LZT_WEB_DOCS_ENABLED", "true").strip().lower() != "false"

base = f"https://{domain}" if domain else f"http://127.0.0.1:{port}"

rows = [
    ("Domain", domain or "(none, loopback-only - see docs/deploy.md)"),
    ("Health check", f"{base}/healthz"),
    ("Admin API", f"{base}/subscriptions/create"),
    ("Admin API key", admin_key or "(not set - edit LZT_ADMIN_API_KEY in .env)"),
]
if docs_enabled:
    rows.append(("API reference", f"{base}/scalar"))

for label, value in rows:
    print(f"  {label:<15} {value}")
PY
  printf '%s────────────────────────────────────────────────────%s\n' "$C_BOLD$C_GREEN" "$C_RESET"
  printf '%sManage it:%s scripts/{status,logs,update,stop,restart}.sh\n\n' "$C_DIM" "$C_RESET"
}

if [ -f .env ]; then
  warn ".env already exists — quickstart only prompts once. Edit .env by hand for changes."
  info "continuing straight to scripts/install.sh with the existing .env"
  bash "$LZT_REPO_ROOT/scripts/install.sh"
  print_report
  exit 0
fi

[ -f .env.example ] || die ".env.example missing — cannot bootstrap"
cp .env.example .env
ok "seeded .env from .env.example"

# read_secret <prompt> — hidden input (terminal echo off), printed to stdout.
read_secret() {
  local prompt="$1" value
  read -r -s -p "$prompt" value >&2
  echo >&2
  printf '%s' "$value"
}
# read_plain <prompt> — visible input, printed to stdout.
read_plain() {
  local prompt="$1" value
  read -r -p "$prompt" value
  printf '%s' "$value"
}

phase "lzt.market API token"
info "Get one at https://lzt.market/account/api"
TOKEN="$(read_secret "Token (input hidden, Enter to fill in .env later): ")"
if [ -n "$TOKEN" ]; then
  python3 - "$TOKEN" <<'PY'
import json
import pathlib
import sys

token = sys.argv[1]
p = pathlib.Path(".env")
content = p.read_text()
content = content.replace(
    'LZT_TOKENS=["paste-your-token-here"]',
    f"LZT_TOKENS={json.dumps([token])}",
    1,
)
p.write_text(content)
PY
  ok "token saved to .env"
else
  warn "no token entered — the daemon starts but can't poll until LZT_TOKENS is set in .env"
fi

phase "Admin API key"
ADMIN_KEY="$(openssl rand -hex 32)"
python3 - "$ADMIN_KEY" <<'PY'
import pathlib
import sys

key = sys.argv[1]
p = pathlib.Path(".env")
content = p.read_text()
content = content.replace(
    "LZT_ADMIN_API_KEY=replace-with-openssl-rand-hex-32",
    f"LZT_ADMIN_API_KEY={key}",
    1,
)
p.write_text(content)
PY
ok "generated (openssl rand -hex 32) and saved to .env — not printed here"

phase "Domain + automatic TLS (optional)"
DOMAIN="$(read_plain "Public domain pointing at this server, or Enter to skip: ")"
if [ -n "$DOMAIN" ]; then
  EMAIL="$(read_plain "Contact email for Let's Encrypt: ")"
  [ -n "$EMAIL" ] || die "a domain needs a contact email for the ACME account — re-run and provide one"
  python3 - "$DOMAIN" "$EMAIL" <<'PY'
import pathlib
import sys

domain, email = sys.argv[1], sys.argv[2]
p = pathlib.Path(".env")
content = p.read_text()
content = content.replace("LZT_DOMAIN=", f"LZT_DOMAIN={domain}", 1)
content = content.replace("LZT_ACME_EMAIL=", f"LZT_ACME_EMAIL={email}", 1)
p.write_text(content)
PY
  ok "$DOMAIN configured — install.sh will issue a real Let's Encrypt cert via nginx+certbot"
else
  info "no domain — engine stays loopback-only (see docs/deploy.md to add one later)"
fi

phase "Handing off to scripts/install.sh"
bash "$LZT_REPO_ROOT/scripts/install.sh"
print_report
