"""End-to-end smoke test for scripts/run_pipeline.py.

Every external endpoint (Greenhouse/Lever/Ashby/Adzuna/Tavily/Postmark) is
mocked via a single httpx.MockTransport, keyed by hostname AND (for the ATS
boards) the requested slug — a 404 for any unrecognized slug, matching real
ATS behavior for a company with no board there. This matters specifically
for self-expansion: without slug-awareness, a generic "any Lever request
returns a non-empty board" mock would make self-expansion falsely "confirm"
a board for every untracked company Adzuna surfaces, including ones that
shouldn't resolve to anything.

The profile is never the real profile.yaml — run_pipeline()'s `profile_path`
param points at a tmp file per test, built from profile.example.yaml with a
fictional seed_companies list. Self-expansion discoveries land in the tmp
state DB (company_expansion_attempts), not back in the profile. The scoring
and company-research LLM agents are TestModel-overridden automatically by
tests/conftest.py's autouse fixture, same as every other unit test. This
only checks the orchestration wires together and completes without raising
— the individual stages already have their own focused unit tests (tier1,
state, sources, post_process, digest, postmark, retry, self_expansion).

JobSpy doesn't go through httpx — python-jobspy uses its own HTTP client
internally, so httpx.MockTransport never intercepts it. Discovered the hard
way (2026-07-24): without an explicit patch, these tests were making real,
slow network calls to Indeed/LinkedIn/Glassdoor/ZipRecruiter on every
routine `pytest` run. The autouse fixture below patches
agent.sources.jobspy_source.scrape_jobs directly instead.
"""

import json
import re
import sqlite3
from pathlib import Path

import httpx
import pandas as pd
import pytest
import yaml

from agent import state
from agent.sources import jobspy_source
from scripts.run_pipeline import run_pipeline

_EXAMPLE_PROFILE = Path(__file__).resolve().parent.parent / "profile.example.yaml"

JOBSPY_FIXTURE_ROWS = [
    {
        "id": "in-fake123",
        "title": "Staff Software Engineer",
        "company": "Fictional JobSpy Co",
        "location": "Remote",
        "is_remote": True,
        "date_posted": "2026-07-20",
        "min_amount": None,
        "max_amount": None,
        "currency": None,
        "interval": None,
        "description": "Build agentic AI systems in Python.",
        "job_url": "https://www.indeed.com/viewjob?jk=fake123",
        "job_url_direct": None,
    }
]


@pytest.fixture(autouse=True)
def mock_jobspy(monkeypatch):
    """Never let a test in this file hit a real job board through JobSpy."""

    def fake_scrape_jobs(**kwargs):
        return pd.DataFrame(JOBSPY_FIXTURE_ROWS)

    monkeypatch.setattr(jobspy_source, "scrape_jobs", fake_scrape_jobs)


@pytest.fixture(autouse=True)
def force_optional_credentials(monkeypatch):
    """Exercise the Adzuna, Tavily, and Postmark code paths regardless of the
    ambient environment. Without this, a run with no local .env skips those
    branches (`if settings.adzuna_app_id ...`) and the self-expansion and
    email-subject tests can't reach what they assert on. Every endpoint is
    still mocked via httpx.MockTransport."""
    from agent.config import settings

    for attr, value in {
        "adzuna_app_id": "test-app-id",
        "adzuna_api_key": "test-api-key",
        "tavily_api_key": "test-tavily-key",
        "postmark_server_token": "test-postmark-token",
        "email_from": "digest@example.com",
        "email_to": "you@example.com",
    }.items():
        monkeypatch.setattr(settings, attr, value)


GREENHOUSE_SLUG = "fictionalgreenhouseco"
LEVER_SLUG = "fictionalleverco"
ASHBY_SLUG = "fictionalashbyco"

SEED_COMPANIES = [
    {
        "name": "Fictional Greenhouse Co",
        "ats_type": "greenhouse",
        "board_token": GREENHOUSE_SLUG,
        "domain": "fictionalgreenhouseco.com",
    },
    {
        "name": "Fictional Lever Co",
        "ats_type": "lever",
        "board_token": LEVER_SLUG,
        "domain": "fictionalleverco.com",
    },
    {
        "name": "Fictional Ashby Co",
        "ats_type": "ashby",
        "board_token": ASHBY_SLUG,
        "domain": "fictionalashbyco.com",
    },
]

GREENHOUSE_FIXTURE = {
    "jobs": [
        {
            "id": 1,
            "title": "Staff Software Engineer",
            "updated_at": "2026-07-01T00:00:00-04:00",
            "location": {"name": "Remote - US"},
            "content": "Build agentic AI systems in Python.",
            "absolute_url": "https://boards.greenhouse.io/example/jobs/1",
            "departments": [{"name": "Engineering"}],
            "company_name": "Fictional Greenhouse Co",
        }
    ]
}

LEVER_FIXTURE = [
    {
        "id": "abc",
        "text": "Staff Software Engineer",
        "categories": {"team": "Engineering", "location": "Remote"},
        "descriptionPlain": "Build agentic AI systems in Python.",
        "hostedUrl": "https://jobs.lever.co/example/abc",
        "createdAt": 1751328000000,
        "workplaceType": "remote",
    }
]

ASHBY_FIXTURE = {
    "jobs": [
        {
            "id": "xyz",
            "title": "Staff Software Engineer",
            "location": "Remote - US",
            "isRemote": True,
            "descriptionPlain": "Build agentic AI systems in Python.",
            "department": "Engineering",
            "jobUrl": "https://jobs.ashbyhq.com/example/xyz",
            "publishedAt": "2026-07-01T00:00:00.000Z",
            "compensation": {},
        }
    ]
}

ADZUNA_FIXTURE = {
    "results": [
        {
            "id": "999",
            "title": "Staff Machine Learning Engineer",
            "company": {"display_name": "Adzuna Example Co"},
            "location": {"display_name": "Remote"},
            "description": "Build ML infra and agentic pipelines.",
            "redirect_url": "https://www.adzuna.com/details/999",
            "created": "2026-07-01T00:00:00Z",
            "category": {"label": "IT Jobs"},
        }
    ]
}

TAVILY_FIXTURE = {
    "results": [{"title": "Example Co news", "url": "https://example.com/news", "content": "..."}]
}


def _write_profile(tmp_path, seed_companies=None):
    """A tmp profile.yaml: profile.example.yaml with a fictional seed list."""
    data = yaml.safe_load(_EXAMPLE_PROFILE.read_text())
    data["seed_companies"] = SEED_COMPANIES if seed_companies is None else seed_companies
    path = tmp_path / "profile.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


def _mock_handler(request: httpx.Request) -> httpx.Response:
    host = request.url.host
    path = request.url.path

    if host == "boards-api.greenhouse.io":
        slug = path.split("/")[-2]  # /v1/boards/{slug}/jobs
        return (
            httpx.Response(200, json=GREENHOUSE_FIXTURE)
            if slug == GREENHOUSE_SLUG
            else httpx.Response(404)
        )
    if host == "api.lever.co":
        slug = path.split("/")[-1]  # /v0/postings/{slug}
        return (
            httpx.Response(200, json=LEVER_FIXTURE) if slug == LEVER_SLUG else httpx.Response(404)
        )
    if host == "api.ashbyhq.com":
        slug = path.split("/")[-1]  # /posting-api/job-board/{slug}
        return (
            httpx.Response(200, json=ASHBY_FIXTURE) if slug == ASHBY_SLUG else httpx.Response(404)
        )
    if host == "api.adzuna.com":
        return httpx.Response(200, json=ADZUNA_FIXTURE)
    if host == "api.tavily.com":
        return httpx.Response(200, json=TAVILY_FIXTURE)
    if host == "api.postmarkapp.com":
        return httpx.Response(200, json={"MessageID": "abc-123", "ErrorCode": 0})

    raise AssertionError(f"Unexpected request to unmocked host: {host}")


async def test_run_pipeline_completes_without_raising(tmp_path):
    db_path = tmp_path / "seen_jobs.db"
    profile_path = _write_profile(tmp_path)

    async with httpx.AsyncClient(transport=httpx.MockTransport(_mock_handler)) as client:
        await run_pipeline(client=client, db_path=db_path, profile_path=profile_path)

    assert db_path.exists()


async def test_run_pipeline_is_idempotent_on_second_run(tmp_path):
    """Second run should treat everything as already-seen and not re-score."""
    db_path = tmp_path / "seen_jobs.db"
    profile_path = _write_profile(tmp_path)

    async with httpx.AsyncClient(transport=httpx.MockTransport(_mock_handler)) as client:
        await run_pipeline(client=client, db_path=db_path, profile_path=profile_path)
        await run_pipeline(client=client, db_path=db_path, profile_path=profile_path)

    assert db_path.exists()


async def test_cross_source_duplicate_is_collapsed(tmp_path):
    """Same real job ("Fictional Greenhouse Co", "Staff Software Engineer",
    "Remote - US") appearing via both Greenhouse (direct, from the seed list)
    and Adzuna (aggregator) should collapse to one entry before scoring,
    keeping the direct ATS source.
    """
    db_path = tmp_path / "seen_jobs.db"
    profile_path = _write_profile(tmp_path)

    def handler_with_duplicate(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.adzuna.com":
            fixture_with_duplicate = {
                "results": [
                    *ADZUNA_FIXTURE["results"],
                    {
                        "id": "888",
                        "title": "Staff Software Engineer",
                        "company": {"display_name": "Fictional Greenhouse Co"},
                        "location": {"display_name": "Remote - US"},
                        "description": "Duplicate of the Greenhouse posting.",
                        "redirect_url": "https://www.adzuna.com/details/888",
                        "created": "2026-07-01T00:00:00Z",
                        "category": {"label": "IT Jobs"},
                    },
                ]
            }
            return httpx.Response(200, json=fixture_with_duplicate)
        return _mock_handler(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler_with_duplicate)) as client:
        await run_pipeline(client=client, db_path=db_path, profile_path=profile_path)

    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT source, company FROM seen_jobs WHERE company = 'Fictional Greenhouse Co'"
    ).fetchall()
    conn.close()

    # Only the Greenhouse-sourced posting recorded — the Adzuna duplicate
    # was collapsed before ever reaching tier1/scoring/recording.
    assert rows == [("greenhouse", "Fictional Greenhouse Co")]


async def test_self_expansion_leaves_untrackable_company_alone(tmp_path):
    """ "Adzuna Example Co" (from ADZUNA_FIXTURE) has no real board in this
    scenario — every ATS mock 404s for its guessed slug. Self-expansion must
    not record a confirmed board for it — regression test for a real bug
    (2026-07-24) where a hostname-only mock caused self-expansion to falsely
    "confirm" a board for every untracked company.
    """
    db_path = tmp_path / "seen_jobs.db"
    profile_path = _write_profile(tmp_path)

    async with httpx.AsyncClient(transport=httpx.MockTransport(_mock_handler)) as client:
        await run_pipeline(client=client, db_path=db_path, profile_path=profile_path)

    conn = sqlite3.connect(db_path)
    try:
        assert state.get_discovered_companies(conn) == []
    finally:
        conn.close()


async def test_self_expansion_adds_confirmed_company(tmp_path):
    """ "Adzuna Example Co" DOES have a real Lever board in this scenario —
    self-expansion should find it and record it in the state DB.
    """
    db_path = tmp_path / "seen_jobs.db"
    profile_path = _write_profile(tmp_path)

    def handler_with_findable_company(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.lever.co":
            slug = request.url.path.split("/")[-1]
            if slug == "adzunaexampleco":
                return httpx.Response(
                    200,
                    json=[
                        {
                            "id": "found",
                            "text": "Some Role",
                            "categories": {},
                            "descriptionPlain": "...",
                            "hostedUrl": "https://jobs.lever.co/adzunaexampleco/found",
                            "createdAt": 1751328000000,
                        }
                    ],
                )
        return _mock_handler(request)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler_with_findable_company)
    ) as client:
        await run_pipeline(client=client, db_path=db_path, profile_path=profile_path)

    conn = sqlite3.connect(db_path)
    try:
        discovered = {c["name"]: c for c in state.get_discovered_companies(conn)}
    finally:
        conn.close()
    assert "adzuna example co" in discovered
    added = discovered["adzuna example co"]
    assert added["ats_type"] == "lever"
    assert added["board_token"] == "adzunaexampleco"


async def test_self_expansion_works_through_jobspy(tmp_path):
    """A company discovered only via JobSpy (not Adzuna) should still trigger
    self-expansion and be recorded in the state DB if confirmed — the same
    logic must work through JobSpy, not just Adzuna.
    """
    db_path = tmp_path / "seen_jobs.db"
    profile_path = _write_profile(tmp_path)

    def handler_confirming_jobspy_company(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.ashbyhq.com":
            slug = request.url.path.split("/")[-1]
            if slug == "fictionaljobspyco":
                return httpx.Response(
                    200,
                    json={
                        "jobs": [
                            {
                                "id": "found",
                                "title": "Some Role",
                                "location": "Remote",
                                "isRemote": True,
                                "descriptionPlain": "...",
                                "jobUrl": "https://jobs.ashbyhq.com/fictionaljobspyco/found",
                                "publishedAt": "2026-07-01T00:00:00.000Z",
                                "compensation": {},
                            }
                        ]
                    },
                )
        return _mock_handler(request)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler_confirming_jobspy_company)
    ) as client:
        await run_pipeline(client=client, db_path=db_path, profile_path=profile_path)

    conn = sqlite3.connect(db_path)
    try:
        discovered = {c["name"]: c for c in state.get_discovered_companies(conn)}
    finally:
        conn.close()
    assert "fictional jobspy co" in discovered  # from JOBSPY_FIXTURE_ROWS, not the seed list
    added = discovered["fictional jobspy co"]
    assert added["ats_type"] == "ashby"
    assert added["board_token"] == "fictionaljobspyco"


async def test_cross_source_dedup_collapses_jobspy_duplicate(tmp_path, monkeypatch):
    """Same real job ("Fictional Greenhouse Co", "Staff Software Engineer",
    "Remote - US") appearing via both Greenhouse (direct) and JobSpy
    (scraped aggregator) should collapse to one entry, keeping the direct
    ATS source — cross-source dedup must work with JobSpy too, not just
    Adzuna.
    """
    db_path = tmp_path / "seen_jobs.db"
    profile_path = _write_profile(tmp_path)

    def fake_scrape_jobs_with_duplicate(**kwargs):
        return pd.DataFrame(
            [
                {
                    "id": "li-dup",
                    "title": "Staff Software Engineer",
                    "company": "Fictional Greenhouse Co",
                    "location": "Remote - US",
                    "is_remote": True,
                    "date_posted": "2026-07-01",
                    "min_amount": None,
                    "max_amount": None,
                    "currency": None,
                    "interval": None,
                    "description": "Duplicate of the Greenhouse posting, scraped via JobSpy.",
                    "job_url": "https://www.linkedin.com/jobs/view/dup",
                    "job_url_direct": None,
                }
            ]
        )

    monkeypatch.setattr(jobspy_source, "scrape_jobs", fake_scrape_jobs_with_duplicate)

    async with httpx.AsyncClient(transport=httpx.MockTransport(_mock_handler)) as client:
        await run_pipeline(client=client, db_path=db_path, profile_path=profile_path)

    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT source, company FROM seen_jobs WHERE company = 'Fictional Greenhouse Co'"
    ).fetchall()
    conn.close()

    # Only the Greenhouse-sourced posting recorded — the JobSpy duplicate
    # was collapsed before ever reaching tier1/scoring/recording.
    assert rows == [("greenhouse", "Fictional Greenhouse Co")]


async def test_digest_email_subject_format(tmp_path):
    """Subject line must be "Job Digest {date} - {number} found". The exact
    count isn't asserted here (TestModel's structured
    output for dealbreaker/comp/level fields varies run to run, so how many
    entries survive hard-reject isn't deterministic) — only the format.
    """
    db_path = tmp_path / "seen_jobs.db"
    profile_path = _write_profile(tmp_path)
    captured_subject = {}

    def handler_capturing_postmark(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.postmarkapp.com":
            captured_subject["value"] = json.loads(request.content)["Subject"]
            return httpx.Response(200, json={"MessageID": "abc-123", "ErrorCode": 0})
        return _mock_handler(request)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler_capturing_postmark)
    ) as client:
        await run_pipeline(client=client, db_path=db_path, profile_path=profile_path)

    assert "value" in captured_subject
    assert re.fullmatch(r"Job Digest \d{4}-\d{2}-\d{2} - \d+ found", captured_subject["value"])
