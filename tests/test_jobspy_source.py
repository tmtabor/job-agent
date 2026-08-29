"""Unit tests for JobSpy source normalization.

Only normalize_jobspy() is tested here — a pure function over fixture row
dicts matching python-jobspy's real DataFrame-row shape (verified live
2026-07-24). fetch_jobspy_raw() isn't unit tested: it wraps a third-party
scraper (not an httpx call we control), same convention as fetch_*_raw()
for the other sources.
"""

import json
from pathlib import Path

from agent.sources.jobspy_source import normalize_jobspy

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_rows() -> list[dict]:
    return json.loads((FIXTURES_DIR / "jobspy_rows.json").read_text())


def test_normalize_jobspy_basic_fields():
    postings = normalize_jobspy(_load_rows())

    assert len(postings) == 2
    staff_eng = postings[0]
    assert staff_eng.source == "jobspy"
    assert staff_eng.source_native_id == "in-abc123"
    assert staff_eng.company == "Example Co"
    assert staff_eng.title == "Staff Software Engineer"
    assert staff_eng.remote is True
    assert staff_eng.compensation_text == "USD 300,000 - 340,000 (yearly)"
    # job_url_direct preferred over job_url
    assert staff_eng.apply_url == "https://careers.example.com/jobs/abc123"
    assert staff_eng.updated_at is not None


def test_normalize_jobspy_handles_missing_company_and_comp():
    postings = normalize_jobspy(_load_rows())

    office_mgr = postings[1]
    assert office_mgr.company == "Unknown"  # null company -> fallback
    assert office_mgr.remote is False
    assert office_mgr.compensation_text is None
    # job_url_direct is null -> falls back to job_url
    assert office_mgr.apply_url == "https://www.linkedin.com/jobs/view/xyz789"


def test_normalize_jobspy_remote_true_flag_overrides_location_text():
    rows = [
        {
            "id": "test-1",
            "title": "Engineer",
            "company": "Test Co",
            "location": "New York, NY",  # doesn't mention "remote"
            "is_remote": True,  # but the flag says it is
            "description": "...",
            "job_url": "https://example.com/1",
        }
    ]
    postings = normalize_jobspy(rows)
    assert postings[0].remote is True


def test_normalize_jobspy_falls_back_to_location_text_when_flag_missing():
    rows = [
        {
            "id": "test-2",
            "title": "Engineer",
            "company": "Test Co",
            "location": "Fully Remote",
            "is_remote": None,
            "description": "...",
            "job_url": "https://example.com/2",
        }
    ]
    postings = normalize_jobspy(rows)
    assert postings[0].remote is True


def test_normalize_jobspy_single_sided_compensation():
    rows = [
        {
            "id": "test-3",
            "title": "Engineer",
            "company": "Test Co",
            "location": "Remote",
            "is_remote": True,
            "description": "...",
            "job_url": "https://example.com/3",
            "min_amount": 250000,
            "max_amount": None,
            "currency": "USD",
            "interval": "yearly",
        }
    ]
    postings = normalize_jobspy(rows)
    assert postings[0].compensation_text == "USD 250,000 (yearly)"
