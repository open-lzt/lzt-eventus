# Deploying the event engine

<p align="right"><b>English</b> · <a href="deploy.md">Русский</a></p>

Step-by-step guide to running the `event_engine` daemon on a server, with an
optional public domain + automatically-renewed TLS certificate in front of the
admin API and inbound webhook. Written to work on a **completely empty**
server — the scripts install everything they need (Docker, `uv`, and, if you
configure a domain, nginx + certbot).

Two deploy modes exist, pick one:

- **Docker Compose** (recommended) — `scripts/install.sh` drives
  `deploy/docker-compose.yml`, which runs Postgres, Redis and the engine in
  isolated containers.
- **Bare-metal / VM** — `deploy/lzt-core.service` runs the daemon directly via
  `uv`, against a Postgres/Redis you install and manage yourself.

Both share the same `.env` file. TLS (below) is the same mechanism for both:
host-level nginx + certbot, wired by `scripts/setup_tls.sh`.

## Quickstart (for the lazy — one command)

```bash
git clone https://github.com/open-lzt/lzt-eventus.git lzt-core && cd lzt-core && scripts/quickstart.sh
```

Interactively prompts for the lzt.market token, an optional domain + contact
email, generates `LZT_ADMIN_API_KEY` itself, then hands off to `install.sh`
for everything scriptable. Ends with a report — health-check URL, admin API
key (shown once, same convention as webhook secrets), docs link. Re-running
it after `.env` already exists skips straight to `install.sh` (edit `.env` by
hand for changes). Everything below this section is what `quickstart.sh`
does for you automatically — read on for the manual/scriptable path, or to
understand what each step means.

## Prerequisites

- A server (VM or bare-metal) with a public IP. `install.sh` assumes a
  Debian/Ubuntu host (`apt-get`) and installs Docker + `uv` itself if absent —
  nothing needs to be pre-installed.
- lzt.market API token(s) — https://lzt.market/account/api
- **Optional, for a public domain**: a domain/subdomain with an A (and/or
  AAAA) record pointing at the server's IP, and inbound TCP 80 + 443 reachable
  from the internet (Let's Encrypt's HTTP-01 challenge needs port 80). Without
  a domain, everything below still works — the engine stays loopback-only,
  reachable over SSH tunnel or a private network instead of HTTPS.

Every port the stack publishes (`LZT_HEALTH_PORT`, `LZT_POSTGRES_PORT`,
`LZT_REDIS_PORT`) defaults to a specific, non-standard 5-digit number (27543 / 27542 / 27541 — see `.env.example`), not 9189/5432/6379-style
conventions. A shared box already running other projects is far less likely
to own an odd number like that than the well-known default — pick different
values in `.env` only if you actually hit a collision (`scripts/install.sh`
checks and warns loudly, it never silently rebinds).

```bash
git clone https://github.com/open-lzt/lzt-eventus.git lzt-core
cd lzt-core
cp .env.example .env
```

Edit `.env`:

```ini
LZT_TOKENS=["your-real-token"]
LZT_ADMIN_API_KEY=<paste the output of: openssl rand -hex 32>
LZT_CATEGORIES=["steam"]                    # JSON array of categories to poll

# Only if you have a domain (see "Domain + automatic TLS" below):
LZT_DOMAIN=events.example.com
LZT_ACME_EMAIL=you@example.com
```

## Option A — Docker Compose (`scripts/install.sh`)

```bash
scripts/install.sh
```

One idempotent command: installs Docker + `uv` if missing, seeds `.env` from
`.env.example` if absent, brings up Postgres + Redis, runs Alembic
migrations, builds and starts the engine, health-gates it, and — if
`LZT_DOMAIN` is set in `.env` — runs `scripts/setup_tls.sh` to get it a
trusted certificate. Safe to re-run any time (`--no-start` provisions stores
+ migrates without starting the engine).

Verify:

```bash
curl -s http://127.0.0.1:27543/healthz
scripts/status.sh
```

The engine port is published to `127.0.0.1` only — it carries no TLS on its
own, so it's not meant to be exposed directly to the internet. Set
`LZT_DOMAIN` (below) to make it reachable over HTTPS, or leave it
loopback-only and reach it over an SSH tunnel / private network.

## Option B — Bare-metal / VM (systemd)

Requires `uv`, and a Postgres 16 + Redis 7 you run yourself (matching
`LZT_DATABASE_URL` / `LZT_REDIS_URL` in `.env`).

```bash
sudo mkdir -p /opt/lzt-core
sudo cp -r . /opt/lzt-core          # or clone directly into /opt/lzt-core
sudo cp .env /opt/lzt-core/.env
sudo useradd --system --home /opt/lzt-core lzt || true
sudo chown -R lzt:lzt /opt/lzt-core

sudo cp deploy/lzt-core.service /etc/systemd/system/lzt-core.service
sudo systemctl daemon-reload
sudo systemctl enable --now lzt-core
```

`ExecStartPre` runs `uv sync` on every start, so `systemctl restart lzt-core`
after a `git pull` is enough to pick up new dependencies. Verify:

```bash
systemctl status lzt-core
curl -s http://127.0.0.1:27543/healthz
```

### Auto-update (both modes)

`deploy/autoupdate.yml` + `scripts/autoupdate.py` poll `git_ref`, and on a new
commit: pull → `uv sync` → `alembic upgrade` → run the `pytest -m e2e` gate →
restart → health-check → automatic rollback if `/healthz` doesn't recover.
**Disabled by default** (`enabled: false`) — this is intentional, don't flip
it on unless you want unattended rollouts. To enable on bare-metal:

```bash
sudo cp deploy/lzt-core-autoupdate.service deploy/lzt-core-autoupdate.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now lzt-core-autoupdate.timer
```

Set `enabled: true` in `deploy/autoupdate.yml` first — the timer no-ops
otherwise. Turn it back to `false` when you're done testing it; the shipped
default (and every fresh clone) has it off.

## Domain + automatic TLS

Fronts the engine with **host-level nginx + certbot**
(`scripts/setup_tls.sh`), not a container — this is deliberate: a box that
already runs other sites on 80/443 (a shared VPS, a box with an existing
reverse proxy) gets **one more vhost** added, not a second process fighting
for the same ports. On a genuinely empty box, the script installs nginx +
certbot itself. Same script, same result either way.

Prerequisites: `LZT_DOMAIN` resolves to this server
(`dig +short $LZT_DOMAIN @1.1.1.1` matches the server's IP, checked from the
server — not your laptop) and `LZT_ACME_EMAIL` is set in `.env`.

```bash
scripts/setup_tls.sh
```

This: installs `nginx`/`certbot`/`python3-certbot-nginx` if absent, writes
`/etc/nginx/sites-available/$LZT_DOMAIN.conf` proxying to
`127.0.0.1:$LZT_HEALTH_PORT` (default 27543), symlinks it into
`sites-enabled`, reloads nginx, then runs certbot's nginx plugin to issue the
certificate and rewrite the vhost for HTTPS + redirect. It's already invoked
automatically by `scripts/install.sh` when `LZT_DOMAIN` is set — run it
standalone only to add/renew TLS after the fact (e.g. you set `LZT_DOMAIN`
post-install) or to re-apply the vhost if you hand-edited it.

Verify:

```bash
curl -s https://$LZT_DOMAIN/healthz
```

### Notes

- Without `LZT_DOMAIN` set, the engine stays loopback-only — `setup_tls.sh`
  no-ops (both `install.sh` and standalone runs).
- **certbot owns renewal** — the Debian/Ubuntu package installs its own
  `certbot.timer`/cron entry on install; nothing extra to wire up.
- **Multi-tenant box:** `setup_tls.sh` only ever writes
  `sites-available/$LZT_DOMAIN.conf` and its symlink — it never touches other
  vhosts, and certbot only ever requests a cert for `$LZT_DOMAIN`. Confirmed
  safe alongside other nginx-fronted sites on the same host.
- Rate limits: Let's Encrypt caps certificate requests per domain per week.
  Don't re-run `setup_tls.sh` in a loop while debugging DNS — fix DNS first
  (`dig`), then issue the cert once.
- The admin API (`LZT_ADMIN_API_KEY`) and inbound webhook
  (`LZT_LOLZ_WEBHOOK_SECRET`) are still auth-gated behind nginx — TLS
  terminates transport encryption, it doesn't replace those checks.

## Firewall

Only if you manage the firewall yourself (a fresh box usually has none
active) — **check `ufw status` first**; don't blind-enable it on a box that
already has other services relying on existing rules:

```bash
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw allow OpenSSH
sudo ufw enable
```

Do **not** open 27543 externally — it's plaintext HTTP; reach it only via
nginx (443) or `127.0.0.1`.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `certbot --nginx` fails `Timeout during connect` | `LZT_DOMAIN` doesn't resolve to this server's IP yet — check `dig +short $LZT_DOMAIN @1.1.1.1` from the server, wait for DNS propagation. |
| `too many certificates already issued` | Let's Encrypt rate limit — stop retrying, wait out the window (their docs list the exact duration). |
| `/healthz` fails after `scripts/install.sh` | Engine itself is unhealthy, not a TLS issue — check `scripts/logs.sh` first. |
| Autoupdate rolls back every run | `e2e_gate: true` failed — the freshly-pulled commit fails `pytest -m e2e`; fix the test/regression before the next rollout. |
| `docker compose` errors on relative paths | Compose ≥ 5 resolves `context:`/`env_file:`/volume paths relative to `--project-directory`, not the compose file — `scripts/_lib.sh`'s `compose()` already passes an absolute `-f` path so the project directory resolves correctly; don't invoke `docker compose` directly with a relative `-f` from a different `cwd`. |
| `pydantic_settings.exceptions.SettingsError` parsing `categories` | `LZT_CATEGORIES` must be a JSON array (`["steam"]`), same as `LZT_TOKENS` — there's no comma-separated parser despite what older docs said. |
| `ModuleNotFoundError: No module named 'webhook_engine'` inside the container | Fixed as of this repo's current `deploy/Dockerfile` (it `COPY libs ./libs` and installs `--no-editable`) — if you've forked/vendored the Dockerfile, make sure both are present: hatchling's editable-install hook only emits a `.pth` for `src/`, silently dropping the `libs/webhook_engine` workspace package even though `pyproject.toml`'s wheel `packages` list names it. |
| `ModuleNotFoundError: No module named 'psycopg2'` on daemon boot (migrations succeeded fine) | A bare `postgresql://` DSN makes SQLAlchemy pick the sync psycopg2 driver by default; `alembic/env.py` already rewrote it to `postgresql+asyncpg://` defensively, `event_engine.orm.base.build_async_sessionmaker` now does the same — fixed as of this repo's current `src/event_engine/orm/base.py`. |
| Alembic connects to the wrong database (`localhost:5432` instead of the compose network) | A `${LZT_DATABASE_URL:-postgres:5432-default}`-style fallback in `docker-compose.yml`'s `environment:` block is NOT safe — any script that already exported `.env`'s bare-metal DSN into its own shell (`load_env` in `_lib.sh`) makes that shell-inherited value win over the compose-file default when `docker compose` resolves it. The current compose file hardcodes the compose-network DSN unconditionally for exactly this reason — don't reintroduce a `:-` fallback there. |
| `compose run`/`compose up` silently uses a stale image after a source change | Neither rebuilds an already-built image on its own. `install.sh`/`update.sh` both run an explicit `compose build engine` before migrating/restarting — don't skip straight to `compose run`/`up` for either. |
| Loud port-collision warning during `install.sh` on a re-run of your *own* already-up stack | Expected/harmless — `port_in_use` can't distinguish "your own container already listening from a prior run" from a real foreign collision. Only investigate if the warning appears on what should be a genuinely first run. |

## See also

- [Event engine usage guide](usage/event-engine.en.md) — subscriptions, modules, config knobs.
- [Configuration](usage/configuration.en.md) — `EngineConfig` / `ClientConfig` reference.
