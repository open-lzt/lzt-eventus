# Summary

<!-- What changes and why. Link the issue / plan task (e.g. W2-T15). -->

## Type

- [ ] Feature
- [ ] Fix
- [ ] Refactor (no behaviour change)
- [ ] Docs / ops / governance

## Checklist

- [ ] `uv run ruff check src tests` passes
- [ ] `uv run ruff format --check src tests` passes
- [ ] `uv run mypy src` passes (strict)
- [ ] `uv run pytest --cov=src --cov-fail-under=80` passes
- [ ] Docs synced (`_MODULE.md` / README / `.env.example` if config changed)
- [ ] No secrets/tokens committed (gitleaks clean; `.env` not staged)
- [ ] Web layer changes use POST/GET only (no PUT/PATCH/DELETE)
- [ ] DTOs at module boundaries; errors chained, never silenced
- [ ] Migrations are reversible (`down()` provided) if the schema changed

## Notes for reviewers

<!-- Anything risky, follow-ups, or out-of-scope items. -->
