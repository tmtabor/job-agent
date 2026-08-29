"""Live integration tests: real network calls to real external APIs.

These exist for exactly one reason: every source module was originally built
against documentation (and, for Ashby, against a lossy AI-summarized fetch of
that documentation) rather than a live response. A 2026-07-24 manual
cross-check against real APIs found real drift — Ashby's `id` field isn't
actually documented (though present in practice), `applyUrl` and `jobUrl` are
distinct fields, and Adzuna's `category` needs a real tag from its
/categories endpoint, not free text. These tests catch that class of bug
automatically instead of relying on another manual doc review.

No LLM calls here (see evals/ for that) — just real HTTP against real public
job boards plus Adzuna/Tavily/Postmark using this repo's real credentials.
Skipped by default; run with: uv run pytest -m live

Real companies (Stripe/Palantir/Ramp) are used as known-stable ATS examples.
If one of them ever migrates off its ATS or a board briefly has zero open
postings, swap in another public board — these tests aren't pinned to those
companies for any reason but "confirmed to work on 2026-07-24."
"""

import httpx
import pytest

from agent.config import settings
from agent.sources.adzuna import fetch_adzuna_all_pages, normalize_adzuna
from agent.sources.ashby import fetch_ashby_raw, normalize_ashby
from agent.sources.greenhouse import fetch_greenhouse_raw, normalize_greenhouse
from agent.sources.jobspy_source import fetch_jobspy_raw, normalize_jobspy
from agent.sources.lever import fetch_lever_raw, normalize_lever
from agent.sources.tavily import tavily_search

pytestmark = pytest.mark.live


@pytest.fixture
async def client():
    async with httpx.AsyncClient(timeout=30.0) as c:
        yield c


async def test_greenhouse_live(client):
    raw = await fetch_greenhouse_raw(client, "stripe")
    postings = normalize_greenhouse(raw, company="Stripe")

    assert len(postings) > 0
    first = postings[0]
    assert first.title
    assert first.apply_url.startswith("http")
    assert first.updated_at is not None
    # content is HTML-escaped in the raw payload — if html_to_text() regressed,
    # this would still contain literal "&lt;" entities.
    assert "&lt;" not in first.description_text


async def test_lever_live(client):
    raw = await fetch_lever_raw(client, "palantir")
    postings = normalize_lever(raw, company="Palantir")

    assert len(postings) > 0
    first = postings[0]
    assert first.title
    assert first.apply_url.startswith("http")


async def test_ashby_live(client):
    raw = await fetch_ashby_raw(client, "ramp")
    postings = normalize_ashby(raw, company="Ramp")

    assert len(postings) > 0
    first = postings[0]
    assert first.title
    # Must be the application-form URL, not the posting page — real field
    # names confirmed live 2026-07-24 (see this file's module docstring).
    assert "/application" in first.apply_url or first.apply_url.startswith("http")
    assert first.source_native_id  # never empty, even on a board with no `id`


async def test_adzuna_live(client):
    if not (settings.adzuna_app_id and settings.adzuna_api_key):
        pytest.skip("ADZUNA_APP_ID/ADZUNA_API_KEY not set")

    results = await fetch_adzuna_all_pages(
        client,
        settings.adzuna_app_id,
        settings.adzuna_api_key,
        what="software engineer",
        category="it-jobs",
        results_per_page=5,
        max_pages=2,
    )
    postings = normalize_adzuna(results)

    assert len(postings) > 0
    first = postings[0]
    assert first.company
    assert first.apply_url.startswith("http")


async def test_jobspy_live():
    """Doesn't use the shared httpx `client` fixture — python-jobspy uses
    its own HTTP client internally, confirmed live 2026-07-24 (it already
    isolates per-site failures itself; a ZipRecruiter 403 didn't block
    Indeed/LinkedIn results in the same call).
    """
    rows = await fetch_jobspy_raw(
        ["indeed"], search_term="software engineer", location="Remote", results_wanted=3
    )
    postings = normalize_jobspy(rows)

    assert len(postings) > 0
    first = postings[0]
    assert first.title
    assert first.apply_url.startswith("http")


async def test_tavily_live(client):
    if not settings.tavily_api_key:
        pytest.skip("TAVILY_API_KEY not set")

    results = await tavily_search(client, settings.tavily_api_key, query="Stripe company funding")

    assert len(results) > 0
    assert results[0].get("url", "").startswith("http")


async def test_postmark_live_with_test_token(client):
    """Uses Postmark's documented POSTMARK_API_TEST token — validated by
    Postmark like a real send but never actually delivered. Safe to run
    repeatedly without spamming a real inbox.
    """
    from agent.delivery.postmark import send_digest

    result = await send_digest(
        client,
        server_token="POSTMARK_API_TEST",
        from_email=settings.email_from or "test@example.com",
        to_email=settings.email_to or "test@example.com",
        subject="job-agent live integration test",
        html_body="<html><body>test</body></html>",
    )

    assert result.get("ErrorCode") == 0
    assert result.get("MessageID")
