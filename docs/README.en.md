# lzt-eventus documentation

<p align="right"><b>English</b> · <a href="README.md">Русский</a></p>

Typed, token-pooled async SDK over the lzt.market catalog API, plus an optional
durable **event engine** (poll → diff → replayable log → catch-up bus).

The README is the entry door; this is the house. Everything a consumer imports is in
`pylzt.__all__` — the stable public surface.

## Usage

- [Quickstart](usage/quickstart.en.md) — install, build a `Client`, first read.
- [Reading the catalog](usage/catalog.en.md) — lots, filters, pagination, batch, bound `refresh()`.
- [Configuration & dependency injection](usage/configuration.en.md) — `ClientConfig`, swapping transport / cache / proxy / retry / metrics / token selection.
- [Error handling](usage/errors.en.md) — the typed error tree, retries, the check() registry.
- [Event engine](usage/event-engine.en.md) — subscribe to catalog events and run the daemon.

## Deploying

- [Deploy guide](deploy.en.md) — Docker Compose vs bare-metal, auto-update, domain + automatic TLS.
- [Гайд по деплою](deploy.md) — то же самое на русском.

## Extending

- [Extension points](extending.en.md) — the seam map: subclass a base, inject it, never fork.
- The `lzt-extending` skill is the AI-agent deep guide to the same seams.

## Install

```bash
pip install "git+https://github.com/zlexdev/aiolzt.git"                            # the pylzt SDK alone
pip install "lzt-eventus[engine] @ git+https://github.com/open-lzt/lzt-eventus.git"  # + durable stores + daemon runtime (postgres/redis/fastapi)
```

`import pylzt` performs **zero I/O** — `httpx` is imported lazily only when a `Client`/
`HttpxSession` actually sends a request.
