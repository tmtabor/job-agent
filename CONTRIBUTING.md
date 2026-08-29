# Contributing

This is a personal project published as a reference. Issues and PRs are welcome, but there's no support guarantee and the roadmap is "whatever the author needs."

## Development

```bash
uv sync --group dev
uv run ruff check .
uv run ruff format .
uv run pytest                 # unit tests — no network, no API key
uv run pytest -m eval         # real Gemini calls — costs money
uv run pytest -m live         # real external APIs, no LLM — free, slower
```

CI runs lint, format check, `profile.example.yaml` validation, and the unit suite on every push and PR.

## Conventions

- **No personal data in the repo.** Per-search config lives in `profile.yaml` (gitignored). `profile.example.yaml` is a *fictional* candidate — keep it that way. Don't commit anyone's résumé details, comp figures, or an actual job-search shortlist. (Well-known public company names as neutral test scaffolding — sort-order fixtures, ATS slug-derivation examples — are fine.)
- The unit and eval suites bind to `profile.example.yaml`. If you change its comp bars or dealbreakers, update `evals/fixtures/jobs.json` to match (and re-run `-m eval`).
- `pyproject.toml` sets `asyncio_default_test_loop_scope = "session"` deliberately — see `CLAUDE.md`. Don't revert it without re-verifying `-m eval` with more than one real-call test.
- `CLAUDE.md` documents the non-obvious design decisions; update it when you change one.
