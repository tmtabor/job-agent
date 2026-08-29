"""Unit tests for source normalize functions.

Only normalize_* is tested here — pure functions, no network. fetch_*_raw()
is not unit tested; it's exercised at real-run time and is a thin httpx call
with no branching logic of its own worth mocking.
"""

import json
from pathlib import Path

from agent.sources.adzuna import normalize_adzuna
from agent.sources.ashby import normalize_ashby
from agent.sources.common import html_to_text, looks_remote
from agent.sources.greenhouse import normalize_greenhouse
from agent.sources.lever import normalize_lever

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load(name: str):
    return json.loads((FIXTURES_DIR / name).read_text())


def test_html_to_text_unescapes_and_strips_tags():
    raw = "&lt;p&gt;Build our &lt;strong&gt;agentic AI&lt;/strong&gt; platform.&lt;/p&gt;"
    assert html_to_text(raw) == "Build our agentic AI platform."


def test_html_to_text_handles_none_and_empty():
    assert html_to_text(None) == ""
    assert html_to_text("") == ""


def test_looks_remote_matches_case_insensitively():
    assert looks_remote("Remote - US")
    assert looks_remote("San Diego", "Fully REMOTE team")
    assert not looks_remote("San Diego, CA", "Engineering")
    assert not looks_remote(None, None)


def test_normalize_greenhouse():
    raw = _load("greenhouse_response.json")
    postings = normalize_greenhouse(raw, company="Fictional Co")

    assert len(postings) == 2
    staff_eng = postings[0]
    assert staff_eng.source == "greenhouse"
    assert staff_eng.source_native_id == "4567890"
    assert staff_eng.company == "Fictional Co"
    assert staff_eng.title == "Staff Software Engineer, Agent Platform"
    assert staff_eng.remote is True
    assert staff_eng.department == "Engineering"
    assert "agentic AI" in staff_eng.description_text
    assert "<strong>" not in staff_eng.description_text
    assert staff_eng.apply_url.endswith("/4567890")
    assert staff_eng.updated_at is not None

    office_mgr = postings[1]
    assert office_mgr.remote is False
    assert office_mgr.location == "San Diego, CA"


def test_normalize_lever():
    raw = _load("lever_response.json")
    postings = normalize_lever(raw, company="Fictional Co")

    assert len(postings) == 2
    staff_eng = postings[0]
    assert staff_eng.source == "lever"
    assert staff_eng.source_native_id == "abc-123"
    assert staff_eng.remote is True  # workplaceType == "remote"
    assert staff_eng.department == "Engineering"
    assert staff_eng.description_text == "Own our LLM agent orchestration layer end to end."
    assert staff_eng.updated_at is not None

    ae = postings[1]
    assert ae.remote is False
    assert ae.location == "New York, NY"


def test_normalize_ashby():
    raw = _load("ashby_response.json")
    postings = normalize_ashby(raw, company="Fictional Co")

    assert len(postings) == 3
    ai_eng = postings[0]
    assert ai_eng.source == "ashby"
    assert ai_eng.remote is True
    assert ai_eng.compensation_text == "$300,000 - $340,000 base"
    # apply_url must be the applyUrl (application form), not jobUrl (posting page)
    assert ai_eng.apply_url.endswith("/application")

    coordinator = postings[1]
    assert coordinator.remote is False
    assert coordinator.compensation_text is None


def test_normalize_ashby_falls_back_to_apply_url_when_id_missing():
    """Ashby's own docs don't document an `id` field on the job object —
    verified missing in practice would otherwise raise a KeyError.
    """
    raw = _load("ashby_response.json")
    postings = normalize_ashby(raw, company="Fictional Co")

    no_id_job = postings[2]
    assert (
        no_id_job.source_native_id == "https://jobs.ashbyhq.com/fictionalco/no-id-job/application"
    )


def test_normalize_ashby_workplace_type_remote_counts_as_remote():
    """isRemote=false but workplaceType="Remote" should still count as remote."""
    raw = _load("ashby_response.json")
    postings = normalize_ashby(raw, company="Fictional Co")

    no_id_job = postings[2]
    assert no_id_job.remote is True


def test_normalize_adzuna():
    raw = _load("adzuna_response.json")
    postings = normalize_adzuna(raw["results"])

    assert len(postings) == 2
    ml_eng = postings[0]
    assert ml_eng.source == "adzuna"
    assert ml_eng.company == "Fictional Adzuna Co."
    assert ml_eng.remote is True
    assert ml_eng.compensation_text == "$300,000 - $350,000"

    warehouse = postings[1]
    assert warehouse.remote is False
    assert warehouse.compensation_text is None
