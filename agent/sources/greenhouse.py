"""Greenhouse Job Board API source. No auth required.

Fetch and normalize are kept separate on purpose: normalize_greenhouse() is a
pure function unit-testable against recorded fixture JSON, with no network
involved. fetch_greenhouse_raw() is the one thing that actually hits the
network — retried/skipped uniformly by the orchestrator's retry wrapper
(agent/pipeline/retry.py), not internally here.
"""

from __future__ import annotations

import httpx

from agent.models import Posting
from agent.sources.common import html_to_text, looks_remote

BASE_URL = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"


async def fetch_greenhouse_raw(client: httpx.AsyncClient, board_token: str) -> dict:
    """Fetch the raw jobs payload for one Greenhouse board token."""
    response = await client.get(BASE_URL.format(token=board_token), params={"content": "true"})
    response.raise_for_status()
    return response.json()


def normalize_greenhouse(raw: dict, company: str) -> list[Posting]:
    """Convert a raw Greenhouse `jobs` payload into normalized Postings."""
    postings = []
    for job in raw.get("jobs", []):
        location_name = (job.get("location") or {}).get("name")
        departments = job.get("departments") or []
        department = departments[0].get("name") if departments else None
        description_text = html_to_text(job.get("content"))

        postings.append(
            Posting(
                source="greenhouse",
                source_native_id=str(job["id"]),
                company=company,
                title=job.get("title", ""),
                location=location_name,
                remote=looks_remote(location_name, department),
                department=department,
                description_text=description_text,
                compensation_text=None,  # not exposed by the Greenhouse jobs API
                apply_url=job.get("absolute_url", ""),
                updated_at=job.get("updated_at"),
            )
        )
    return postings
