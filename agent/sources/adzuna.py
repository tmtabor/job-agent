"""Adzuna Jobs API source. Requires app_id + app_key.

Primary breadth source per the plan — free tier is ≈1,000 calls/month, so
callers should use `category` to pre-filter at the query level rather than
pulling everything and filtering client-side. See
agent/sources/greenhouse.py's docstring for why fetch and normalize are kept
separate.
"""

from __future__ import annotations

import httpx

from agent.models import Posting
from agent.sources.common import looks_remote

BASE_URL = "https://api.adzuna.com/v1/api/jobs/{country}/search/{page}"


async def fetch_adzuna_raw(
    client: httpx.AsyncClient,
    app_id: str,
    app_key: str,
    what: str,
    country: str = "us",
    page: int = 1,
    results_per_page: int = 50,
    category: str | None = None,
) -> dict:
    """Fetch one page of Adzuna search results."""
    params = {
        "app_id": app_id,
        "app_key": app_key,
        "what": what,
        "results_per_page": results_per_page,
    }
    if category:
        params["category"] = category
    response = await client.get(BASE_URL.format(country=country, page=page), params=params)
    response.raise_for_status()
    return response.json()


async def fetch_adzuna_all_pages(
    client: httpx.AsyncClient,
    app_id: str,
    app_key: str,
    what: str,
    country: str = "us",
    results_per_page: int = 50,
    category: str | None = None,
    max_pages: int = 3,
) -> list[dict]:
    """Fetch up to `max_pages` of results, stopping early at the last page.

    A page returning fewer than `results_per_page` results means it's the
    last page — no need to keep requesting empty pages after that. `max_pages`
    is a hard cap regardless: Adzuna's free tier is ~1,000 calls/month
    (~33/day), so this must stay small relative to total daily call volume
    across all sources, not just grow to cover every available result.
    """
    all_results: list[dict] = []
    for page in range(1, max_pages + 1):
        raw = await fetch_adzuna_raw(
            client,
            app_id,
            app_key,
            what=what,
            country=country,
            page=page,
            results_per_page=results_per_page,
            category=category,
        )
        results = raw.get("results", [])
        all_results.extend(results)
        if len(results) < results_per_page:
            break
    return all_results


def _compensation_text(job: dict) -> str | None:
    salary_min = job.get("salary_min")
    salary_max = job.get("salary_max")
    if salary_min and salary_max:
        return f"${salary_min:,.0f} - ${salary_max:,.0f}"
    if salary_min or salary_max:
        return f"${(salary_min or salary_max):,.0f}"
    return None


def normalize_adzuna(results: list[dict]) -> list[Posting]:
    """Convert a flat list of raw Adzuna job dicts into normalized Postings.

    Takes a flat list (not the wrapping {"results": [...], "count": ...}
    response dict) because fetch_adzuna_all_pages() merges multiple pages
    together — there's no single meaningful "count"/"mean" once merged.
    """
    postings = []
    for job in results:
        company = (job.get("company") or {}).get("display_name", "Unknown")
        location_name = (job.get("location") or {}).get("display_name")
        department = (job.get("category") or {}).get("label")

        postings.append(
            Posting(
                source="adzuna",
                source_native_id=str(job["id"]),
                company=company,
                title=job.get("title", ""),
                location=location_name,
                remote=looks_remote(location_name, job.get("title")),
                department=department,
                description_text=job.get("description", ""),
                compensation_text=_compensation_text(job),
                apply_url=job.get("redirect_url", ""),
                updated_at=job.get("created"),
            )
        )
    return postings
