<!-- Briefly describe what this change does and why. -->

## Checklist

- [ ] `uv run ruff check .` and `uv run ruff format .` pass
- [ ] `uv run pytest` passes
- [ ] Ran `uv run pytest -m eval` if this touches the scoring prompt, `JobEvaluation` schema, the judge criteria, or `profile.example.yaml`'s comp bars / dealbreakers
- [ ] Docs updated (`README.md` / `CLAUDE.md`) if behavior or configuration changed
- [ ] No personal data in the diff — résumé details, comp figures, a real job-search shortlist, or real emails (see `CONTRIBUTING.md`)
