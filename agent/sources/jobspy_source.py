"""JobSpy source: LinkedIn/Indeed/Glassdoor/ZipRecruiter.

Named jobspy_source.py, not jobspy.py, to avoid any ambiguity with the
`jobspy` package itself.

Uses the python-jobspy package (a scraper, not a REST API) — per the plan,
"isolate in its own try/except — treat as best-effort, non-critical...
expect this to be the flakiest source" (especially LinkedIn, which needs
proxies at real volume). Verified live 2026-07-24: jobspy.scrape_jobs()
already isolates per-site failures internally (a ZipRecruiter 403 didn't
block LinkedIn/Indeed results in the same call), so wrapping the whole call
in the orchestrator's existing retry/skip pattern (agent/pipeline/retry.py)
— same as every other source — is sufficient; no extra per-site handling
needed here.

scrape_jobs() is synchronous (internally does blocking HTTP requests), so
fetch_jobspy_raw() runs it via asyncio.to_thread() rather than blocking the
event loop the rest of the async pipeline shares.
"""

from __future__ import annotations

import asyncio

import pandas as pd
from jobspy import scrape_jobs

from agent.models import Posting
from agent.sources.common import looks_remote


async def fetch_jobspy_raw(
    site_names: list[str],
    search_term: str,
    location: str,
    results_wanted: int = 20,
    hours_old: int = 72,
) -> list[dict]:
    """Scrape postings via python-jobspy. Returns a list of raw row dicts,
    with pandas NaN cleaned to None (jobspy returns a DataFrame).
    """

    def _scrape() -> pd.DataFrame:
        return scrape_jobs(
            site_name=site_names,
            search_term=search_term,
            location=location,
            results_wanted=results_wanted,
            hours_old=hours_old,
        )

    df = await asyncio.to_thread(_scrape)
    df = df.where(pd.notna(df), None)
    return df.to_dict(orient="records")


def _compensation_text(row: dict) -> str | None:
    min_amount = row.get("min_amount")
    max_amount = row.get("max_amount")
    currency = row.get("currency") or ""
    interval = row.get("interval") or ""

    if min_amount and max_amount:
        amount_text = f"{min_amount:,.0f} - {max_amount:,.0f}"
    elif min_amount or max_amount:
        amount_text = f"{min_amount or max_amount:,.0f}"
    else:
        return None

    text = f"{currency} {amount_text}".strip()
    if interval:
        text += f" ({interval})"
    return text


def normalize_jobspy(rows: list[dict]) -> list[Posting]:
    """Convert raw python-jobspy row dicts into normalized Postings."""
    postings = []
    for row in rows:
        location_name = row.get("location")
        # job_url_direct (employer's own posting/ATS) over job_url (the
        # scraped site's own listing page) — same reasoning as preferring
        # Ashby's applyUrl over jobUrl (verified against the live API, 2026-07-24).
        apply_url = row.get("job_url_direct") or row.get("job_url") or ""
        native_id = row.get("id") or apply_url

        postings.append(
            Posting(
                source="jobspy",
                source_native_id=str(native_id),
                company=row.get("company") or "Unknown",
                title=row.get("title") or "",
                location=location_name,
                remote=bool(row.get("is_remote")) or looks_remote(location_name),
                department=None,
                description_text=row.get("description") or "",
                compensation_text=_compensation_text(row),
                apply_url=apply_url,
                updated_at=row.get("date_posted"),
            )
        )
    return postings
