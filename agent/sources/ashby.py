"""Ashby Job Board API source. No auth required.

Best compensation-field support of the three ATS sources per the plan, via
`includeCompensation=true`. See agent/sources/greenhouse.py's docstring for
why fetch and normalize are kept separate.

Field names verified 2026-07-24 against real live boards (ramp, notion,
linear) — Ashby's own published docs don't document an `id` field on the job
object (verified via docs), even though it's present in practice on every
board checked; keep the defensive fallback below rather than assuming it.
`jobUrl` (posting page) and `applyUrl` (application form) are distinct real
fields — `apply_url` should be `applyUrl`, not `jobUrl`.
"""

from __future__ import annotations

import httpx

from agent.models import Posting
from agent.sources.common import html_to_text, looks_remote

BASE_URL = "https://api.ashbyhq.com/posting-api/job-board/{board}"


async def fetch_ashby_raw(client: httpx.AsyncClient, board_name: str) -> dict:
    """Fetch the raw jobs payload for one Ashby job board."""
    response = await client.get(
        BASE_URL.format(board=board_name), params={"includeCompensation": "true"}
    )
    response.raise_for_status()
    return response.json()


def _compensation_text(job: dict) -> str | None:
    compensation = job.get("compensation") or {}
    summary = compensation.get("compensationTierSummary") or compensation.get("summary")
    if summary:
        return summary
    components = compensation.get("summaryComponents") or []
    if components:
        return "; ".join(str(c.get("summary") or c) for c in components if c.get("summary") or c)
    return None


def normalize_ashby(raw: dict, company: str) -> list[Posting]:
    """Convert a raw Ashby `jobs` payload into normalized Postings."""
    postings = []
    for job in raw.get("jobs", []):
        location_name = job.get("location")
        description_text = job.get("descriptionPlain") or html_to_text(job.get("descriptionHtml"))
        apply_url = job.get("applyUrl") or job.get("jobUrl") or ""
        # No id field is documented (though present on every live board
        # checked) — fall back to the apply URL, which is unique per posting.
        native_id = job.get("id") or apply_url

        postings.append(
            Posting(
                source="ashby",
                source_native_id=str(native_id),
                company=company,
                title=job.get("title", ""),
                location=location_name,
                remote=(
                    bool(job.get("isRemote"))
                    or job.get("workplaceType") == "Remote"
                    or looks_remote(location_name)
                ),
                department=job.get("department"),
                description_text=description_text,
                compensation_text=_compensation_text(job),
                apply_url=apply_url,
                updated_at=job.get("publishedAt"),
            )
        )
    return postings
