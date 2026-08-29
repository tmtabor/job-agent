"""The candidate profile: all per-search configuration, loaded from a YAML file.

This is the one place a person's job search is described — the résumé summary
the scoring model reads, the compensation floors, the dealbreakers, the target
titles and locations Tier 1 filters on, and the hand-seeded company list. It is
deliberately *not* committed: `profile.yaml` is gitignored, and
`profile.example.yaml` ships a fictional stand-in that the test and eval suites
bind to.

Loaded once at the top of `run_pipeline()` (before any network call) so a typo
or missing field fails the run immediately rather than mid-scan.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

DEFAULT_PROFILE_PATH = Path(__file__).resolve().parent.parent / "profile.yaml"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CompBars(_Strict):
    """Hard compensation floors, in USD. A role below the applicable bar is
    rejected in post-processing even if everything else fits."""

    local_or_remote_usd: int
    relocation_usd: int
    preferred_domain_floor_usd: int


class SeedCompany(_Strict):
    """One hand-tracked company. Greenhouse/Lever/Ashby have no discovery
    endpoint, so the starting set is maintained here; self-expansion grows the
    effective list at runtime (persisted in the state DB, not written back
    here)."""

    name: str
    ats_type: Literal["greenhouse", "lever", "ashby"]
    board_token: str
    # This company's own canonical domain, used as the company_research cache
    # key — NOT the ATS hosting domain. Null when unknown (e.g. a company only
    # ever seen via a discovery source).
    domain: str | None = None


class Tier1Patterns(_Strict):
    """Regex fragments for the no-LLM Tier 1 pass. Each list is OR-joined and
    compiled case-insensitively by `agent.pipeline.tier1.compile_tier1_patterns`."""

    title_patterns: list[str] = Field(min_length=1)
    description_keywords: list[str] = Field(min_length=1)
    preferred_location_patterns: list[str] = Field(min_length=1)


class SourceQueries(_Strict):
    """Per-source search parameters for the breadth/aggregator sources."""

    jobspy_search_term: str
    jobspy_location: str
    adzuna_query: str
    adzuna_category: str = "it-jobs"


class Profile(_Strict):
    candidate_summary: str
    seniority_targets: str
    dealbreakers: list[str] = Field(min_length=1)
    dealbreaker_notes: str
    preferred_domains: list[str] = Field(min_length=1)
    comp_bars: CompBars
    relocation_cities: list[str] = Field(min_length=1)
    preferred_locations: list[str] = Field(min_length=1)
    tier1: Tier1Patterns
    source_queries: SourceQueries
    seed_companies: list[SeedCompany]


def load_profile(path: Path | None = None) -> Profile:
    """Load and validate a profile YAML file.

    Raises FileNotFoundError with a copy-the-example hint if the file is
    missing, or pydantic ValidationError if it is malformed.
    """
    path = path or DEFAULT_PROFILE_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Copy profile.example.yaml to profile.yaml and edit it "
            "for your own search (see the README 'Setup' section)."
        )
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return Profile.model_validate(data)
