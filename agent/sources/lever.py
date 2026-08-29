"""Lever Postings API source. No auth required.

See agent/sources/greenhouse.py's docstring for why fetch and normalize are
kept separate.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx

from agent.models import Posting
from agent.sources.common import html_to_text, looks_remote

BASE_URL = "https://api.lever.co/v0/postings/{company}"


async def fetch_lever_raw(client: httpx.AsyncClient, company_slug: str) -> list[dict]:
    """Fetch the raw postings list for one Lever company slug."""
    response = await client.get(BASE_URL.format(company=company_slug), params={"mode": "json"})
    response.raise_for_status()
    return response.json()


def normalize_lever(raw: list[dict], company: str) -> list[Posting]:
    """Convert a raw Lever postings list into normalized Postings."""
    postings = []
    for job in raw:
        categories = job.get("categories") or {}
        location_name = categories.get("location")
        workplace_type = job.get("workplaceType")  # "remote" | "hybrid" | "on-site" | None

        description_text = job.get("descriptionPlain") or html_to_text(job.get("description"))

        created_at_ms = job.get("createdAt")
        updated_at = datetime.fromtimestamp(created_at_ms / 1000, tz=UTC) if created_at_ms else None

        postings.append(
            Posting(
                source="lever",
                source_native_id=str(job["id"]),
                company=company,
                title=job.get("text", ""),
                location=location_name,
                remote=(workplace_type == "remote") or looks_remote(location_name),
                department=categories.get("team"),
                description_text=description_text,
                compensation_text=categories.get("salaryDescription"),
                apply_url=job.get("hostedUrl", ""),
                updated_at=updated_at,
            )
        )
    return postings
