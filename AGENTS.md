# Agent / contributor notes

## Cross-repo: keep `lzt-eventus-sdk` and `lzt-admin-panel` in sync

Two **separate, private repositories** consume this repo's `event_engine` management
API (`src/event_engine/web/`) and are not subpackages here / not auto-generated from
this code:

- [`lzt-eventus-sdk`](https://github.com/open-lzt/lzt-eventus-sdk) — an async Python
  client SDK.
- [`lzt-admin-panel`](https://github.com/open-lzt/lzt-admin-panel) — a React 18 +
  TypeScript admin panel (subscriptions, token accounts, event polling, live streams).

**Rule: any change under `src/event_engine/web/` that touches the wire contract —
a route's path/method, a request/response DTO shape (`web/schemas/dtos.py`), an
error code (`web/base/errors.py`), the SSE/WS frame format, or webhook signing/
headers (`libs/webhook_engine/`) — must be accompanied by a matching update to
BOTH `lzt-eventus-sdk` and `lzt-admin-panel` in the same change window.** A route
or field added here with no follow-up in either consumer is an incomplete PR, not
a "later" task.

What "matching update" means, concretely:
- New/changed route or DTO field → update `lzt-eventus-sdk`'s `ManagementClient`
  methods/models (`src/lzt_eventus_sdk/client.py`, `models.py`) AND
  `lzt-admin-panel`'s `src/shared/api/types.ts` + whichever page's `api.ts` calls it.
- New/changed error code → add/update the typed exception in
  `src/lzt_eventus_sdk/errors.py` AND the `ERROR_MESSAGES` map in
  `lzt-admin-panel/src/shared/api/types.ts`.
- Changed SSE/WS/webhook wire format → update the matching transport in
  `src/lzt_eventus_sdk/sources/` or `server/receiver.py` AND
  `lzt-admin-panel/src/pages/streams/useEventStream.ts`.
- Re-capture `lzt-eventus-sdk/tests/fixtures/api_captures.json` (or the SSE/WS
  fixtures) from a live `TestClient(build_app(...))` run — the SDK's tests assert
  against real captured server responses, not hand-written guesses (see
  `lzt-eventus-sdk/CONTRIBUTING.md`). A stale fixture is a false "still passing"
  signal, not a green light.

If you don't have access to the `lzt-eventus-sdk` or `lzt-admin-panel` checkout in
the current session, say so explicitly and flag the needed follow-up in the PR
description — don't silently ship a one-sided API change.

## Everything else

See `CONTRIBUTING.md` for setup, the local CI gate, and code conventions.
