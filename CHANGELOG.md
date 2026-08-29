# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-08-28

Initial public release.

### Added

- Daily GitHub Actions pipeline that fetches job postings from Greenhouse, Lever, Ashby,
  Adzuna, and JobSpy; filters cheaply with no LLM (Tier 1); scores survivors against a
  candidate profile with Gemini (Tier 2); enriches scoring with cached per-company research
  (PTO, stability, RTO reality, dealbreaker screening); and emails a ranked digest.
- All per-search configuration — the candidate summary, compensation floors, dealbreakers,
  target titles and locations, and the tracked-company seed list — in a single gitignored
  `profile.yaml`, validated on load. `profile.example.yaml` ships a fictional reference.
- Company self-expansion: confirmed ATS boards for companies first seen via Adzuna/JobSpy are
  recorded in the state DB and merged into the tracked list on subsequent runs.
- SQLite state (dedupe history, cached company research, dealbreaker blocklist, expansion
  attempts), gitignored and rebuilt on first run; carried between CI runs via `actions/cache`.
- Three test tiers: fast unit tests against `TestModel` and mocked HTTP (no key, no cost),
  `-m eval` (real Gemini calls against labeled fixtures), and `-m live` (real external APIs,
  no LLM).
- Logfire instrumentation with automatic console fallback.

[0.1.0]: https://github.com/tmtabor/job-agent/releases/tag/v0.1.0
