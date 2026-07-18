# Contributing to lzt-core

Thanks for helping. This repo ships two packages: `lzt_core` (SDK) and
`event_engine` (the daemon). Python 3.12, managed by [uv](https://docs.astral.sh/uv/).

## Setup

```bash
git clone https://github.com/open-lzt/lzt-eventus
cd lzt-core
uv sync --extra dev --extra engine --extra lolzteam
```

`uv` creates and manages the `.venv` for you. Prefix commands with `uv run`.

## The local CI floor

Run the exact gate CI enforces before opening a PR:

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy src                       # strict
uv run pytest --cov=src --cov-fail-under=80
```

`scripts/test.sh` runs all of the above in one shot.

## Conventions

- **Layered.** Handler (extract → call service → return) → Service (business
  logic, DI via constructor) → Repository (CRUD behind an ABC) → Model. Never skip
  a layer.
- **Feature-colocated.** A feature lives in one package
  (`<area>/<feature>/{service.py, errors.py, dtos.py, repo.py, _MODULE.md}`).
  No `utils.py` / `helpers.py` dump files.
- **Typed boundaries.** DTOs (dataclass/Pydantic) across every module boundary —
  never a raw `dict`/`tuple`. Money is `Decimal`; datetimes are UTC + tzinfo.
- **Errors are sacred.** Catch the specific type, chain with `raise X(...) from e`,
  carry args (not pre-formatted text). Never `except: pass`.
- **No secrets in code.** Tokens/keys come from env (`LZT_*`). Add any new var to
  `.env.example`. gitleaks runs in CI.
- **Web layer is POST/GET only** (no PUT/PATCH/DELETE — CI enforces this).
- **Migrations are reversible.** Provide `down()` for every Alembic revision.

## The review ritual (run before pushing)

1. **Correctness** — ruff + mypy + tests, walk happy *and* error paths.
2. **Cleanup** — no dead code, debug prints, TODOs, or commented-out blocks.
3. **Architecture** — layers respected, DTOs at boundaries, the pattern is named.
4. **Docs** — sync `_MODULE.md` / README / `.env.example` for what you touched.
5. **Regression sweep** — re-read the diff; check sibling callers and concurrent
   paths.

## Pull requests

- One logical change per PR. Fill in the PR template checklist.
- Touching `src/lzt_core/`, `src/event_engine/`, or `.github/` requires a
  CODEOWNERS review.
- `main` is branch-protected: CI green + review required before merge.
