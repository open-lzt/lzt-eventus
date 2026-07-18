# AI-agent docs — module map

<p align="right"><b>English</b> · <a href="index.md">Русский</a></p>

Condensed pointer set for an agent working in this repo. Read `_MODULE.md` (hand-written intent
+ gotchas) before `_MODULE_AUTO.md` (generated surface listing) before source — in that order.
Full narrative docs for humans live in [`../`](../README.en.md); this page exists so an agent doesn't
have to reverse-engineer the tree from scratch.

## Top-level entry points

- [`src/lzt_eventus/_MODULE.md`](../../src/lzt_eventus/_MODULE.md) — the engine's own map:
  layering rules, why some consumers colocate by feature instead of by primitive type, the
  Memory/Postgres symmetry decision.
- [`src/lzt_eventus/engine.py`](../../src/lzt_eventus/engine.py) — `EventEngine`, the composition
  root. `build()` (Postgres/Redis daemon) vs `build_memory()` (embedded, zero-infra, live-polling).
- [`src/lzt_eventus/devkit/_MODULE.md`](../../src/lzt_eventus/devkit/_MODULE.md) — `local_eventus()`,
  the one-call quickstart that stands up a real engine **and** its management API on an ephemeral
  port for scripts/examples/tests. Progressive-disclosure sibling of `build_memory()` — see
  `~/.claude/skills/library-design/SKILL.md` Law 30 if you're extending this pattern elsewhere.
- [`src/lzt_eventus/web/`](../../src/lzt_eventus/web/) — the management API (FastAPI): routes,
  DTOs, subscription/token-account repos. **Wire-contract-frozen** — see
  [`../../AGENTS.md`](../../AGENTS.md) before touching routes/DTOs/error-codes/SSE-WS-webhook
  format; changes here must ship with a matching `lzt-eventus-sdk` update in a separate repo.
- [`src/lzt_eventus/delivery/subscription_scope.py`](../../src/lzt_eventus/delivery/subscription_scope.py) —
  the typed subscription filter (`NoScope` / `CategoryScope` / `AccountScope`) and which
  `EventType`s each can match.

## Per-submodule maps (`_MODULE.md` where present)

`account/` · `baseline/` · `bus/` · `codecs/` · `consumers/` · `cursor/` · `daemon/` · `dedup/` ·
`delivery/` · `devkit/` · `diff/` · `events/` · `log/` · `orm/` · `sources/` · `transport.py` ·
`web/{base,middlewares,orm,repos,routes,schemas,services,shared}/` — each has (or
should have) a sibling `_MODULE.md`; if one is missing or stale, treat that as a bug and open/flag
it rather than reverse-engineering silently.

## Examples

- [`examples/autobuy/_MODULE.md`](../../examples/autobuy/_MODULE.md) — a ~10-line consumer built
  on `local_eventus` + `lzt-eventus-sdk`: subscribe on a category filter, buy on each match,
  count purchases. The canonical "10 lines of real logic, rest is boilerplate" reference for this
  repo's own progressive-disclosure philosophy.

## Skills (deep, task-shaped guides)

- [`.claude/skills/lzt-integration/SKILL.md`](../../.claude/skills/lzt-integration/SKILL.md) — use
  the library (read catalog, subscribe in-process, receive webhooks, poll for pending events).
- [`.claude/skills/lzt-extending/SKILL.md`](../../.claude/skills/lzt-extending/SKILL.md) — extend
  the core by subclass + inject (new event type, route, source, store/transport backend).

## Architecture & scope

- [`../architecture.md`](../architecture.en.md) — current architecture.
- [`../../ROADMAP.md`](../../ROADMAP.md) — scope and non-goals.
- [`../../AGENTS.md`](../../AGENTS.md) — cross-repo wire-contract rule (read before touching `web/`).
