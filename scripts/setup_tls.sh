#!/usr/bin/env bash
# Domain + automatic TLS: nginx (host-level, reused if already present for other
# sites) reverse-proxies LZT_DOMAIN to the loopback engine port; certbot's nginx
# plugin issues + auto-renews the Let's Encrypt certificate. Idempotent — safe to
# re-run; only touches the one vhost file for LZT_DOMAIN, never other sites.
#
# No-op if LZT_DOMAIN is unset/placeholder (the engine then stays loopback-only,
# see docs/deploy.md).
set -euo pipefail
source "$(dirname "$0")/_lib.sh"
cd "$LZT_REPO_ROOT"
load_env

usage() { cat <<'EOF'
setup_tls.sh — reverse-proxy + Let's Encrypt cert for LZT_DOMAIN via nginx+certbot.

Usage: scripts/setup_tls.sh [--help]
Reads LZT_DOMAIN / LZT_ACME_EMAIL / LZT_HEALTH_PORT from .env. No-op if
LZT_DOMAIN is unset. Installs nginx + certbot if absent (apt). Requires root
(or passwordless sudo) to write /etc/nginx and run certbot.
EOF
}
for a in "$@"; do case "$a" in
  --help) usage; exit 0 ;;
  *) die "unknown arg: $a (see --help)" ;;
esac; done

DOMAIN="${LZT_DOMAIN:-}"
case "$DOMAIN" in
  ""|localhost) info "LZT_DOMAIN not set — skipping TLS setup (engine stays loopback-only)"; exit 0 ;;
esac
[ -n "${LZT_ACME_EMAIL:-}" ] || die "LZT_DOMAIN is set but LZT_ACME_EMAIL is empty — Let's Encrypt requires a contact email"
PORT="${LZT_HEALTH_PORT:-27543}"

SUDO=""
if [ "$(id -u)" -ne 0 ]; then
  need sudo "run as root, or install sudo"
  SUDO="sudo"
fi

phase "System packages (nginx, certbot)"
if ! command -v nginx >/dev/null 2>&1; then
  $SUDO apt-get update -qq
  $SUDO apt-get install -y -qq nginx
  ok "nginx installed"
else
  info "nginx already present"
fi
if ! command -v certbot >/dev/null 2>&1; then
  $SUDO apt-get update -qq
  $SUDO apt-get install -y -qq certbot python3-certbot-nginx
  ok "certbot installed"
else
  info "certbot already present"
fi

phase "nginx vhost for $DOMAIN"
VHOST="/etc/nginx/sites-available/${DOMAIN}.conf"
$SUDO tee "$VHOST" >/dev/null <<NGINXEOF
# lzt-core — managed by scripts/setup_tls.sh. Safe to re-run; only this file changes.
server {
    listen 80;
    listen [::]:80;
    server_name ${DOMAIN};

    location / {
        proxy_pass http://127.0.0.1:${PORT};
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
NGINXEOF
$SUDO mkdir -p /etc/nginx/sites-enabled
if [ ! -L "/etc/nginx/sites-enabled/${DOMAIN}.conf" ]; then
  $SUDO ln -s "$VHOST" "/etc/nginx/sites-enabled/${DOMAIN}.conf"
fi
$SUDO nginx -t
$SUDO systemctl reload nginx 2>/dev/null || $SUDO systemctl start nginx
ok "nginx vhost live on :80 (HTTP only, pre-cert)"

phase "Let's Encrypt certificate"
$SUDO certbot --nginx --non-interactive --agree-tos --redirect \
  -m "$LZT_ACME_EMAIL" -d "$DOMAIN"
ok "certificate issued/renewed for $DOMAIN — certbot's own timer handles renewal"

phase "Verify"
if curl -fsS -o /dev/null "https://${DOMAIN}/healthz"; then
  ok "https://${DOMAIN}/healthz responds"
else
  warn "https://${DOMAIN}/healthz did not respond — check: scripts/logs.sh, and nginx -t"
fi
