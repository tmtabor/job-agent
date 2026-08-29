"""Cross-source de-duplication.

Distinct from agent/state.py's job_id-based seen_jobs tracking, which only
catches a posting re-appearing from the *same* source across runs. This
module catches the same real job appearing from *different* sources — most
commonly a company's own ATS board and Adzuna's aggregation of that same
posting — both within a single run's fetched batch (dedupe_cross_source) and
across runs (agent.state.is_fingerprint_seen, checked by the caller).
"""

from __future__ import annotations

from agent.models import Posting

# Direct ATS boards are authoritative for their own postings — richer,
# cleaner data than an aggregator's copy of the same listing. Lower rank
# wins when two sources report the same content_fingerprint. Ties (e.g. two
# results from the same aggregator) keep whichever was encountered first.
_SOURCE_RANK = {
    "greenhouse": 0,
    "lever": 0,
    "ashby": 0,
    "adzuna": 1,
    "jobspy": 2,
}


def dedupe_cross_source(postings: list[Posting]) -> list[Posting]:
    """Collapse postings that are the same real job from different sources.

    Keeps one representative per content_fingerprint, preferring the most
    authoritative source per _SOURCE_RANK. Order of the input list is not
    preserved for collapsed groups; callers that need a stable order should
    re-sort afterward.
    """
    best_by_fingerprint: dict[str, Posting] = {}
    for posting in postings:
        fingerprint = posting.content_fingerprint
        existing = best_by_fingerprint.get(fingerprint)
        if existing is None or _SOURCE_RANK.get(posting.source, 99) < _SOURCE_RANK.get(
            existing.source, 99
        ):
            best_by_fingerprint[fingerprint] = posting
    return list(best_by_fingerprint.values())
