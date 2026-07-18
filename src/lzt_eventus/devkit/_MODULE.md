# lzt_eventus/devkit/

One-call local runtime: a live engine **and** its management API standing up together
behind a single `async with`, for embedding, examples and tests. The progressive-
disclosure quickstart (library-design Law 30) for the web/subscription side —
the sibling of `EventEngine.build_memory()`, which is the same quickstart for the
engine side.

## Public surface
- `local_eventus(*, client|tokens, config=None, consumers=(), extra_sources=(),
  host="127.0.0.1", port=0, admin_api_key=None, log_level="warning") -> async CM
  yielding LocalEventus` — wires a REAL live-polling `EventEngine.build_memory(...)`,
  builds the FastAPI app (`web.main.build_app`) over the **same** stores the engine
  writes, and runs engine + uvicorn concurrently under a `TaskGroup` the caller owns.
- `LocalEventus(base_url, api_key, engine)` — frozen handle. Feed `base_url`+`api_key`
  to any management client (e.g. `lzt_eventus_sdk.ManagementClient`); `engine` is the
  live instance for runtime tweaks (`add_source`, `add_module`).

## Why it exists
The web layer needs an `EngineHandle` with 8 wired fields + `build_app` + a uvicorn
server + concurrent engine/server supervision. That wiring previously lived only
duplicated between `__main__.py::_run` (production, Postgres, SIGTERM-owned) and a
test-only fake handle over a *fake* engine. `local_eventus` promotes it to a first-
class, production-quality quickstart over the *real* in-memory engine, so live polling
of the actual LZT market happens with zero infra.

## Design notes (Law 30 / Law 29)
- **Same stores, one engine (Law 2).** `event_log`/`cursors` on the handle are
  `engine.stores.log`/`engine.stores.cursor` — the API reads exactly what the engine
  produced, unlike the old fake handle which used separate stores.
- **Embedded host adapter (Law 29).** No owned event loop, no signal handlers
  (uvicorn's are disabled) — the caller's `async with` owns lifecycle and cancellation.
  `build()`/`__main__.py` remain the standalone-daemon adapter; this is the embedded one.
- **Every seam stays overridable.** `client`, `config`, `consumers`, `extra_sources`
  pass straight through to `build_memory`; the power user drops to
  `build_memory(...)` + `build_app(EngineHandle(...))` (the two calls this wraps).
- **Dev-only secrets.** A fixed in-memory Fernet key + a default admin key are injected
  only when the config leaves them empty; an explicit `admin_api_key`/config value wins.
  Never a production path — `build()` fails loud on a missing `LZT_TOKEN_ENC_KEY` instead.

## Gotchas
- `config.categories` (default `[STEAM]`) selects which catalogs the engine polls — a
  subscription only sees events for a category the engine is actually watching.
- Requires the `engine` extra (`fastapi`, `uvicorn`) — `uvicorn`/`build_app` are
  imported lazily inside the function, so `import lzt_eventus` stays I/O- and
  fastapi-free (Law 23).

## See also
- `src/lzt_eventus/engine.py` — `EventEngine.build_memory()` (engine-side quickstart).
- `src/lzt_eventus/web/main.py` — `build_app`; `web/shared/handle.py` — `EngineHandle`.
- `examples/autobuy/` — a ~10-line autobuy core built on `local_eventus`.
