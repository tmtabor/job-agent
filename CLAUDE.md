# CLAUDE.md

A daily job-scanning agent: fetches postings from multiple sources, filters cheaply (no LLM), scores survivors against a configurable candidate profile with an LLM, enriches scoring with cached per-company research, and emails a ranked digest. The code is the source of truth for current behavior; this file covers what isn't obvious from reading it.

## Commands

```bash
uv sync --group dev                     # install deps
uv run pytest                           # unit tests only — no network, no API key needed
uv run pytest -m eval                   # evals — real model calls, costs money
uv run pytest -m live                   # live integration tests — real external APIs, no LLM calls
uv run ruff check .                     # lint
uv run ruff format .                    # format
uv run python scripts/run_pipeline.py   # run the real pipeline (real DB, real send)
```

`asyncio_default_test_loop_scope = "session"` in `pyproject.toml` (not the pytest-asyncio default of `"function"`) — `pydantic-ai`'s Google model (the default) caches a persistent httpx client across calls, which breaks under a fresh event loop per test when two real eval tests run back to back in one session. Don't revert this without re-verifying `-m eval` still works with more than one real-call test in the file.

## The candidate profile (`profile.yaml`)

All per-search config lives in a gitignored **`profile.yaml`**, loaded and validated by `agent/profile.py`'s `load_profile()` (fails fast — `FileNotFoundError` with a copy-the-example hint if missing, `ValidationError` if malformed, `extra="forbid"` on every model so a typo is caught). `profile.example.yaml` is committed — a fictional candidate — and **the unit and eval suites bind to it** (`tests/test_tier1.py`, `evals/conftest.py`). `.env` is secrets only.

- The scoring system prompt is a `string.Template` in `agent/prompts/system.txt` with four `${...}` slots (`candidate_profile`, `seniority_targets`, `hard_dealbreakers`, `comp_bars`, `location_list`). `render_system_prompt(profile)` fills them; `.substitute` is strict, so a new `${slot}` with no matching kwarg raises at render time by design. The generic rubric text stays inline in the file.
- `agent/agents/single.py`'s scoring agent has **no static instructions** — the rendered prompt rides on `AgentDeps.system_prompt` and is returned by a dynamic `@agent.instructions` function, so `score_posting(..., system_prompt=...)` is a required kwarg. `company_research` still uses the plain `load_prompt("company_research")` (no personal data).
- `agent/pipeline/tier1.py` compiles `profile.tier1`'s regex-fragment lists into a `Tier1Patterns` via `compile_tier1_patterns(profile)`, passed as `tier1_filter`'s third arg. The structural guardrail regexes (`_EXCLUDED_ENGINEER_PREFIX`, `_RESTRICTION_PHRASE`, `_US_QUALIFIER`) stay in code.
- `agent/models.py`'s `JobEvaluation.comp_bar_used` is `Literal["local_or_remote", "relocation", "preferred_domain"]` — the tier name, not the dollar amount (amounts live in `profile.comp_bars`).

## Pipeline (`scripts/run_pipeline.py`)

```
fetch_sources -> self_expansion -> cross_source_dedupe -> tier1_filter ->
company_research -> tier2_score -> post_process -> digest_build -> deliver -> persist_state
```

- **Sources** (`agent/sources/`): Greenhouse/Lever/Ashby (direct, from `profile.yaml`'s `seed_companies`), Adzuna (breadth aggregator, requires `ADZUNA_APP_ID` + `ADZUNA_API_KEY` — two separate credentials), JobSpy (LinkedIn/Indeed/Glassdoor/ZipRecruiter scraper via the `python-jobspy` package — its own HTTP client, not `httpx`; see Testing below). Each `fetch_*` is a thin network call; `normalize_*` is a pure function converting the source's raw shape into a `Posting`.
- **Self-expansion** (`agent/pipeline/self_expansion.py`): when Adzuna or JobSpy surfaces a company not already tracked, probes each ATS's standard board-URL pattern for a couple of slug guesses; a confirmed match is recorded in the state DB (`company_expansion_attempts`, via `state.record_expansion_attempt`) and merged with `profile.yaml`'s `seed_companies` at the start of the *next* run (`run_pipeline.merge_companies`, which reads `state.get_discovered_companies`). Seed entries win a name collision (they carry the real `domain`). A board that later 404s is re-probed and its `found_ats_type` reset to NULL, so it drops out of the merged list. Greenhouse exposes a `company_name` field per job used to reject same-slug-different-company false positives; Lever/Ashby don't, so a non-empty board there is accepted as a weaker signal. 30-day cooldown per company (`agent.state.should_attempt_expansion`), capped attempts per run (`MAX_EXPANSION_ATTEMPTS_PER_RUN`).
- **Cross-source dedupe** (`agent/pipeline/dedupe.py`): `Posting.content_fingerprint` (normalized company+title+location) catches the same real job from different sources — both within one run (`dedupe_cross_source`, preferring a direct ATS board over an aggregator) and across runs (`agent.state.is_fingerprint_seen`). Distinct from `Posting.job_id` (hash of source+native-id), which only catches the same source repeating.
- **Tier 1** (`agent/pipeline/tier1.py`): no-LLM pass/ambiguous/reject. Title regex is the primary signal; the description-keyword backstop deliberately excludes generic AI buzzwords (`agent`, `agentic`, `llm`) since a company whose product *is* AI will mention them in every posting's boilerplate regardless of role relevance — only specific implementation vocabulary (`python`, `rag`, `distributed systems`, etc.) counts.
- **Two LLM agents**, both single-shot (no tool-calling — the pipeline's own Python code decides what to fetch/call next, not the model): `agent/agents/single.py` (Tier 2 scoring, `JobEvaluation` output) and `agent/agents/company_research.py` (per-company research synthesis, `CompanyResearchOutput` output, cached in SQLite with a 14-day refresh cooldown on the most volatile field).
- **Dealbreaker scope**: a single posting flagged `dealbreaker: true` excludes *only that posting* from the digest. Only `company_research`'s `dealbreaker_verification` (assessing the company's overall business, not one role) triggers a company-wide Tier 1 blocklist entry. Getting this backwards once blocklisted an entire 400-posting company (including dozens of excellent unrelated roles) because one fellowship posting mentioned a blockchain-security research example.
- **Persistence** (`agent/state.py`): one SQLite file, five tables (`seen_jobs`, `company_research`, `dealbreaker_blocklist`, `dealbreaker_audit_log`, `company_expansion_attempts`) — not split across files despite `company_research` sounding like a separate concern. The file is gitignored and rebuilt on first run (`init_db`, all `CREATE TABLE IF NOT EXISTS`); in CI it round-trips between scheduled runs via `actions/cache`, never a commit.

## Company research query design

`COMPANY_RESEARCH_QUERY_TEMPLATE` in `scripts/run_pipeline.py` is *not* `"{company} company"` (surfaces only Wikipedia-style overview pages, never PTO/benefits/stability content) and deliberately does *not* include "Glassdoor" as a keyword despite it being the obvious first guess — Glassdoor blocks content extraction entirely (verified live: `raw_content` is empty for every Glassdoor URL, always), so biasing toward it wastes result slots on pages that can't be read. `tavily_search()` requests `include_raw_content="text"` since the default short snippet is often too thin to carry real policy detail even from the right URL. `agent/agents/company_research.py`'s `_relevant_excerpt()` extracts one keyword-anchored window *per research topic group* (PTO, layoffs, RTO), not a single global anchor — a single-anchor version was tried and failed: an early, unrelated topic mention wins the anchor and starves out a later, more specific one in the same source. If company research keeps coming back "no reliable information found" for something independently verifiable, suspect this extraction chain before assuming the LLM is hallucination-avoidant to a fault.

## Testing tiers

Three markers, each progressively more expensive — default `pytest` run excludes both `eval` and `live` (`addopts` in `pyproject.toml`):

- **`tests/`** (default): fast, deterministic, zero network, zero cost. TestModel-overridden automatically for every `Agent` under `agent.agents` (`tests/conftest.py`'s autouse fixture) — don't remove that override or a unit test could silently hit a real model. **JobSpy needs its own handling**: `python-jobspy` doesn't go through `httpx`, so `httpx.MockTransport` never intercepts it — mock by monkeypatching `agent.sources.jobspy_source.scrape_jobs` directly (see `tests/test_pipeline_integration.py`'s `mock_jobspy` fixture). Forgetting this once made routine `pytest` runs silently scrape real job boards for 55+ seconds.
- **`evals/`** (`-m eval`): real model calls, costs money. `evals/fixtures/jobs.json` drives the labeled pass/fail dataset eval; `evals/test_llm_judge.py` grades reasoning *quality* (not just field values) using `AGENT_JUDGE_MODEL` — a more capable tier than the one being scored, to avoid self-assessment bias. Every fixture's expected verdict is written against `profile.example.yaml`'s comp bars and dealbreakers (via the `scoring_system_prompt` fixture in `evals/conftest.py`) — change those numbers in the example file and the fixtures need updating too.
- **`tests/test_live_integration.py`** (`-m live`): real network calls to real external APIs (Greenhouse/Lever/Ashby/Adzuna/Tavily/JobSpy/Postmark), no LLM calls. Postmark's test uses the documented `POSTMARK_API_TEST` token — validated like a real send but never delivered, safe to run repeatedly. Exists because every source module was originally built against documentation (or, for Ashby, a lossy AI-summarized fetch of documentation) rather than a live response, and that drifted from reality more than once (Ashby's `applyUrl` vs `jobUrl`, an undocumented-but-present `id` field, Adzuna's `category` needing a real tag from its own `/categories` endpoint, not free text).

## Testability hooks

`run_pipeline()` takes optional `client`, `db_path`, and `profile_path` — all default to the real ones, all injectable for tests. `tests/test_pipeline_integration.py` builds a tmp `profile.yaml` from `profile.example.yaml` with a fictional `seed_companies` list (`_write_profile`); self-expansion discoveries land in the tmp state DB, asserted via `state.get_discovered_companies`.

## Configuration

`agent/config.py` (`Settings`) is model-agnostic — `AGENT_MODEL` / `AGENT_JUDGE_MODEL` take any Pydantic AI model string. `check_provider_key` gives a fail-fast error only for the three common cloud providers (`anthropic`/`openai`/`google`) and only for the agent model; any other provider prefix is left for its own SDK to validate at call time. Defaults are `google:*` so a single `GOOGLE_API_KEY` runs everything including `-m eval`. `tests/conftest.py` sets dummy `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GOOGLE_API_KEY` before importing anything under `agent/`. Other secrets: `TAVILY_API_KEY`, `ADZUNA_APP_ID` + `ADZUNA_API_KEY`, `POSTMARK_SERVER_TOKEN`, `AGENT_EMAIL_FROM`/`AGENT_EMAIL_TO`, optional `LOGFIRE_TOKEN`. Everything non-secret is in `profile.yaml`. `USAGE_LIMITS` is a Python constant near the top of `agent/agents/single.py` and `agent/agents/company_research.py`, not an env var.
