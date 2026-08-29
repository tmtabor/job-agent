"""Shared domain models used across pipeline stages.

These are plain Pydantic models, not agent output types per se, though
JobEvaluation and CompanyResearchOutput are also used as `output_type` for
the scoring and company-research agents respectively (agent/agents/single.py,
agent/agents/company_research.py).
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

# Cross-source dedup normalization: strips common company-suffix noise and
# punctuation so "Acme" == "Acme, Inc." == "ACME" and "Staff Software
# Engineer" == "staff software engineer ", regardless of which source's
# formatting conventions produced the string.
_COMPANY_SUFFIX = re.compile(r"\b(inc|llc|corp|corporation|ltd|co)\.?\s*$")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _normalize_for_fingerprint(text: str | None) -> str:
    if not text:
        return ""
    normalized = text.lower().strip()
    normalized = _COMPANY_SUFFIX.sub("", normalized).strip()
    normalized = _NON_ALNUM.sub(" ", normalized).strip()
    return re.sub(r"\s+", " ", normalized)


class Posting(BaseModel):
    """A job posting normalized from any source into a common shape."""

    source: str  # "greenhouse" | "lever" | "ashby" | "adzuna" | "jobspy"
    source_native_id: str
    company: str
    title: str
    location: str | None
    remote: bool
    department: str | None
    description_text: str
    compensation_text: str | None
    apply_url: str
    updated_at: datetime | None = None

    @property
    def job_id(self) -> str:
        """Stable hash of (source, source_native_id) — the seen_jobs primary key.

        Only catches duplicates within the same source. Use
        content_fingerprint to catch the same real job appearing from
        different sources (e.g. a direct ATS board and Adzuna's aggregation
        of that same posting).
        """
        raw = f"{self.source}:{self.source_native_id}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @property
    def content_fingerprint(self) -> str:
        """Cross-source dedup key: normalized (company, title, location).

        Location is included specifically to avoid collapsing two genuinely
        different open reqs that happen to share a generic title at the same
        company (e.g. "Software Engineer" open in both NYC and Remote) —
        not a perfect guarantee against false-positive collapsing, but a
        meaningful reduction versus (company, title) alone.
        """
        parts = (
            _normalize_for_fingerprint(self.company),
            _normalize_for_fingerprint(self.title),
            _normalize_for_fingerprint(self.location),
        )
        raw = "::".join(parts)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


Tier1Result = Literal["pass", "ambiguous", "reject"]


class CompanyResearchOutput(BaseModel):
    """LLM-synthesized company research.

    Cache metadata (company_domain, last_checked timestamps) lives in the
    persistence layer (agent/state.py), not here — this is only the part an
    LLM call actually produces from Tavily search results.
    """

    pto_type: Literal["generous_accrued", "standard_accrued", "flex_unlimited", "unspecified"]
    pto_evidence: str
    pto_source_url: str | None

    stage_funding: str | None
    stage_source_url: str | None

    stability_signal: Literal["stable", "recent_layoffs", "unclear"]
    stability_evidence: str
    stability_source_url: str | None
    stability_source_date: str | None  # as extracted from source text; not a parsed date

    rto_reality: Literal["matches_stated_policy", "stricter_than_stated", "unclear"]
    rto_evidence: str
    rto_source_url: str | None

    dealbreaker_verification: Literal["clear", "adjacent_signal_found", "none_found"]
    dealbreaker_evidence: str | None
    dealbreaker_source_url: str | None

    eng_leadership_churn: str | None
    oncall_load_signal: str | None


class JobEvaluation(BaseModel):
    """LLM structured evaluation of a single posting (Tier 2 scoring output)."""

    role_family_match: Literal["yes", "no", "ambiguous"]  # only populated when tier1 = 'ambiguous'
    location_match: bool
    location_reasoning: str  # free-text explanation of the location_match verdict
    comp_meets_bar: Literal["yes", "no", "unstated"]
    stated_comp: str | None
    comp_bar_used: Literal["local_or_remote", "relocation", "preferred_domain"]
    level_match: Literal["yes", "no", "ambiguous"]
    level_reasoning: str
    wlb_signal: Literal["strong", "neutral", "concerning", "unclear"]
    wlb_evidence: str
    pto_type: Literal["generous_accrued", "standard_accrued", "flex_unlimited", "unspecified"]
    pto_evidence: str
    # A specific day count, when the posting/research states or implies one
    # (e.g. "15 days PTO", "4 weeks vacation" -> 20). Left null for
    # "unlimited"/"flexible" policies with no stated baseline, or when
    # genuinely unspecified — never a guessed/typical number.
    pto_days_estimate: int | None = Field(default=None, ge=0)
    domain: Literal["dealbreaker", "preferred", "neutral"]
    domain_reasoning: str
    dealbreaker: bool  # explicit veto — excludes this posting from the digest
    dealbreaker_reasoning: str | None
    ai_title_substance: Literal[
        "genuine_ai_ml_work", "generic_role_relabeled", "unclear", "not_applicable"
    ]
    scope_ic_vs_management: Literal["ic", "management_leaning", "unclear"]
    remote_specifics: str  # timezone overlap / travel cadence extracted from posting text
    company_research_conflict: str | None
    # Bounded 1-10 (Field constraint, not just a comment) — observed against a
    # real run 2026-07-24: without ge/le, Gemini occasionally returns a
    # 1-100-scale number (e.g. 85, 75) instead, which was silently accepted
    # and shown as "85/10" in the digest. pydantic-ai retries the model
    # automatically on a validation failure, so this also self-corrects
    # instead of just failing loudly.
    overall_fit_score: int = Field(ge=1, le=10)  # computed last
    summary: str
