"""Tavily search source for company research. Requires an API
key. Feeds raw results as text into the company research agent — never
Gemini's native Search grounding tool (paid-tier only), per the plan.
"""

from __future__ import annotations

import httpx

TAVILY_URL = "https://api.tavily.com/search"


async def tavily_search(
    client: httpx.AsyncClient,
    api_key: str,
    query: str,
    max_results: int = 5,
    include_raw_content: str | bool | None = None,
) -> list[dict]:
    """Run one Tavily search. Returns the raw `results` list.

    Each result has title/url/content (a short snippet) always; with
    `include_raw_content` also `raw_content` (the fuller parsed page text).
    Verified live 2026-07-24: the short `content` snippet is often too thin
    to carry real PTO/benefits detail even from a well-targeted query and
    relevant URL (e.g. Glassdoor's own snippet just confirms a star rating,
    never the actual policy) — `raw_content` from a source that isn't
    blocking scraping (Glassdoor blocks it entirely; job-board mirrors and
    levels.fyi don't) is what actually contains it. Per Tavily's docs, this
    doesn't add credit cost — only `search_depth="advanced"` does (2 credits
    vs. 1) — but that's based on their published docs, not independently
    metered here.
    """
    payload = {"api_key": api_key, "query": query, "max_results": max_results}
    if include_raw_content is not None:
        payload["include_raw_content"] = include_raw_content
    response = await client.post(TAVILY_URL, json=payload)
    response.raise_for_status()
    return response.json().get("results", [])
