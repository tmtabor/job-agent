# job-agent

A daily job-scanning agent. Pulls postings from multiple sources, filters out irrelevant ones cheaply (no LLM), scores the rest against a candidate profile using Gemini, enriches scoring with cached per-company research (PTO, stability, RTO reality, dealbreaker screening), and emails a ranked digest every morning.

> **A personal tool, published as a reference.** This is not a supported product. The scoring rubric, dealbreakers, comp bars, and source choices reflect one person's job search — `profile.example.yaml` is a fictional stand-in. Fork it, rewrite `profile.yaml`, and run your own. BSD-3-Clause; do what you like with it.

## Stack

- Python 3.13, `uv`
- Pydantic AI (two single-shot agents: Tier 2 scoring, company research) + `pydantic-evals`
- SQLite for dedupe/state/caching
- Greenhouse, Lever, Ashby (direct ATS boards), Adzuna (breadth aggregator), JobSpy/LinkedIn+Indeed+Glassdoor+ZipRecruiter (scraper, best-effort)
- Tavily (company research search) + Postmark (email delivery)
- Logfire (observability)

## Setup

```bash
uv sync --group dev

cp .env.example .env            # secrets — API keys, email addresses
cp profile.example.yaml profile.yaml
```

Then edit **`profile.yaml`** — the candidate summary the scoring model reads, your compensation floors, dealbreakers, target job titles, acceptable locations, and the `seed_companies` list of ATS boards to track directly. It is gitignored, and the pipeline fails immediately if it is missing or malformed. `profile.example.yaml` is the documented reference for every field.

`.env` holds only secrets — see [Configuration](#configuration).

## Running it

```bash
uv run python scripts/run_pipeline.py
```

This fetches from every configured source, dedupes, filters, scores, and — if Postmark is configured — sends the digest email. State (dedupe history, cached company research, dealbreaker blocklist, self-expansion discoveries) persists to `data/seen_jobs.db`, which is gitignored and rebuilt automatically on first run. In CI it is carried between scheduled runs via the Actions cache — never committed to the repo.

## Configuration

Secrets are read from the environment (see `.env.example`):

| Variable | Purpose |
|---|---|
| `GOOGLE_API_KEY` | Gemini access — both scoring and company research use it |
| `AGENT_MODEL` | Scoring model, e.g. `google:gemini-3.1-flash-lite` |
| `AGENT_JUDGE_MODEL` | LLM-judge eval model — a more capable Gemini tier than `AGENT_MODEL` |
| `TAVILY_API_KEY` | Company research web search |
| `ADZUNA_APP_ID`, `ADZUNA_API_KEY` | Adzuna breadth source — two separate credentials, not one |
| `POSTMARK_SERVER_TOKEN` | Email delivery |
| `AGENT_EMAIL_FROM`, `AGENT_EMAIL_TO` | Digest sender/recipient |
| `LOGFIRE_TOKEN` | Optional — console output used if unset |

Everything else — profile, comp bars, target titles/locations, per-source search terms, company seed list — lives in `profile.yaml`, not the environment.

## Testing

Three tiers, increasingly expensive:

```bash
uv run pytest              # unit tests — fast, no network, no cost (default)
uv run pytest -m eval       # real Gemini calls against labeled fixtures — costs money
uv run pytest -m live       # real calls to every external API (no LLM) — free but not instant
```

The `eval` and `live` fixtures bind to `profile.example.yaml`, so no personal config is needed to run them.

## How the pipeline fits together

```
fetch (Greenhouse/Lever/Ashby/Adzuna/JobSpy)
  -> self-expansion (probe ATS boards for companies seen via Adzuna/JobSpy;
     confirmed boards persist in the state DB and merge with profile.yaml's
     seed_companies on the next run)
  -> cross-source dedupe (same real job from two sources -> one entry)
  -> Tier 1 filter (no LLM: title/description/location/dealbreaker-blocklist)
  -> company research (cached, Tavily + Gemini, per unique company)
  -> Tier 2 scoring (Gemini, structured JobEvaluation per posting)
  -> post-processing (hard-reject rules, bucket into main/unstated-comp/ambiguous-level)
  -> digest (HTML, ranked, company breakdown at top)
  -> deliver (Postmark)
  -> persist state (SQLite; cached between CI runs, not committed)
```

`CLAUDE.md` covers the non-obvious parts of each stage.

## Self-expansion

Greenhouse/Lever/Ashby have no discovery endpoint — there's no way to enumerate "every company on one of these ATSes." Instead, whenever Adzuna or JobSpy surfaces a company not already tracked, the pipeline probes each ATS's standard board-URL pattern for a plausible slug guess; a confirmed match is recorded in the state DB and merged with `profile.yaml`'s `seed_companies` on subsequent runs. The effective list only grows, starting narrow and broadening over time.

## Scheduling

`.github/workflows/daily-job-scan.yml` runs the pipeline once daily via GitHub Actions. It does **not** commit anything back to the repo:

- **State** (`data/seen_jobs.db`) round-trips between runs via `actions/cache`.
- **`profile.yaml`** is delivered as the `PROFILE_YAML_B64` repository secret (`base64 -i profile.yaml`), written to disk by a workflow step.
- **`.github/workflows/keepalive.yml`** pushes one empty commit a month, because GitHub disables a scheduled workflow after 60 days with no commits.

Required repository secrets: every variable in the [Configuration](#configuration) table, plus `PROFILE_YAML_B64`.

## License

BSD 3-Clause — see [LICENSE](LICENSE).
