"""Unit tests for Adzuna pagination.

httpx.MockTransport only — never a real call. fetch_adzuna_raw() itself
isn't unit tested (thin httpx call, same convention as the other sources),
but fetch_adzuna_all_pages()'s stop-early/max-pages logic has real branching
worth covering directly.
"""

import httpx

from agent.sources.adzuna import fetch_adzuna_all_pages


def _job(job_id: str) -> dict:
    return {
        "id": job_id,
        "title": "Software Engineer",
        "company": {"display_name": "Example Co"},
        "location": {"display_name": "Remote"},
        "description": "...",
        "redirect_url": f"https://www.adzuna.com/details/{job_id}",
        "created": "2026-07-01T00:00:00Z",
        "category": {"label": "IT Jobs"},
    }


def _handler_for_pages(results_by_page: dict[int, list[dict]], requested_pages: list[int]):
    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.path.rsplit("/", 1)[-1])
        requested_pages.append(page)
        results = results_by_page.get(page, [])
        return httpx.Response(200, json={"results": results, "count": 999})

    return handler


async def test_stops_early_when_a_page_is_not_full():
    requested_pages: list[int] = []
    results_by_page = {
        1: [_job("1"), _job("2")],  # full page (results_per_page=2)
        2: [_job("3")],  # short page -> last page, should stop here
        3: [_job("4"), _job("5")],  # would never be requested
    }
    handler = _handler_for_pages(results_by_page, requested_pages)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await fetch_adzuna_all_pages(
            client,
            "app_id",
            "app_key",
            what="software engineer",
            results_per_page=2,
            max_pages=5,
        )

    assert requested_pages == [1, 2]
    assert [j["id"] for j in results] == ["1", "2", "3"]


async def test_respects_max_pages_cap_when_every_page_is_full():
    requested_pages: list[int] = []
    results_by_page = {
        1: [_job("1"), _job("2")],
        2: [_job("3"), _job("4")],
        3: [_job("5"), _job("6")],
        4: [_job("7"), _job("8")],  # would exceed max_pages=3, never requested
    }
    handler = _handler_for_pages(results_by_page, requested_pages)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await fetch_adzuna_all_pages(
            client,
            "app_id",
            "app_key",
            what="software engineer",
            results_per_page=2,
            max_pages=3,
        )

    assert requested_pages == [1, 2, 3]
    assert len(results) == 6


async def test_empty_first_page_returns_empty_list():
    requested_pages: list[int] = []
    handler = _handler_for_pages({}, requested_pages)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await fetch_adzuna_all_pages(
            client,
            "app_id",
            "app_key",
            what="software engineer",
            results_per_page=50,
            max_pages=3,
        )

    assert requested_pages == [1]
    assert results == []
