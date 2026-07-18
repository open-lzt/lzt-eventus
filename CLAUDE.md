# Claude / agent instructions

See [`AGENTS.md`](AGENTS.md) — in particular the cross-repo rule: any change to
`src/event_engine/web/`'s wire contract (routes, DTOs, error codes, SSE/WS/webhook
format) must ship together with a matching update to the separate
[`lzt-eventus-sdk`](https://github.com/open-lzt/lzt-eventus-sdk) repo.

For setup, the local CI gate, and code conventions, see `CONTRIBUTING.md`.
