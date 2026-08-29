"""Unit tests for cross-source de-duplication.

No LLM calls, no network — pure model/function logic.
"""

from agent.models import Posting
from agent.pipeline.dedupe import dedupe_cross_source


def _posting(**overrides) -> Posting:
    defaults = {
        "source": "greenhouse",
        "source_native_id": "1",
        "company": "Anthropic",
        "title": "Staff Software Engineer",
        "location": "Remote",
        "remote": True,
        "department": "Engineering",
        "description_text": "Build things.",
        "compensation_text": None,
        "apply_url": "https://example.com/jobs/1",
    }
    defaults.update(overrides)
    return Posting(**defaults)


# --- content_fingerprint normalization ---


def test_fingerprint_matches_across_company_suffix_variations():
    a = _posting(company="Anthropic")
    b = _posting(company="Anthropic, Inc.")
    c = _posting(company="ANTHROPIC")
    assert a.content_fingerprint == b.content_fingerprint == c.content_fingerprint


def test_fingerprint_matches_across_whitespace_and_case_variations():
    a = _posting(title="Staff Software Engineer")
    b = _posting(title="  staff   software engineer ")
    assert a.content_fingerprint == b.content_fingerprint


def test_fingerprint_differs_for_different_titles():
    a = _posting(title="Staff Software Engineer")
    b = _posting(title="Principal Software Engineer")
    assert a.content_fingerprint != b.content_fingerprint


def test_fingerprint_differs_for_different_companies():
    a = _posting(company="Anthropic")
    b = _posting(company="OpenAI")
    assert a.content_fingerprint != b.content_fingerprint


def test_fingerprint_differs_for_different_locations():
    """Same title at the same company but different locations are probably
    genuinely different open reqs, not the same posting via two sources.
    """
    a = _posting(location="Remote")
    b = _posting(location="San Diego, CA")
    assert a.content_fingerprint != b.content_fingerprint


def test_fingerprint_ignores_source_and_native_id():
    a = _posting(source="greenhouse", source_native_id="123")
    b = _posting(source="adzuna", source_native_id="999")
    assert a.content_fingerprint == b.content_fingerprint
    assert a.job_id != b.job_id  # job_id still differs, by design


# --- dedupe_cross_source ---


def test_dedupe_collapses_same_job_from_two_sources():
    greenhouse_posting = _posting(source="greenhouse", source_native_id="123")
    adzuna_posting = _posting(source="adzuna", source_native_id="999")

    result = dedupe_cross_source([greenhouse_posting, adzuna_posting])

    assert len(result) == 1
    assert result[0].source == "greenhouse"  # direct ATS preferred over aggregator


def test_dedupe_keeps_direct_ats_regardless_of_input_order():
    adzuna_posting = _posting(source="adzuna", source_native_id="999")
    greenhouse_posting = _posting(source="greenhouse", source_native_id="123")

    result = dedupe_cross_source([adzuna_posting, greenhouse_posting])

    assert len(result) == 1
    assert result[0].source == "greenhouse"


def test_dedupe_prefers_direct_ats_over_jobspy():
    """Cross-source dedup must work with JobSpy too, not just Adzuna."""
    jobspy_posting = _posting(source="jobspy", source_native_id="li-999")
    ashby_posting = _posting(source="ashby", source_native_id="abc")

    result = dedupe_cross_source([jobspy_posting, ashby_posting])

    assert len(result) == 1
    assert result[0].source == "ashby"


def test_dedupe_prefers_adzuna_over_jobspy():
    """JobSpy is the lowest-ranked source (the flakiest) — even an aggregator
    like Adzuna is preferred over it.
    """
    jobspy_posting = _posting(source="jobspy", source_native_id="li-999")
    adzuna_posting = _posting(source="adzuna", source_native_id="888")

    result = dedupe_cross_source([jobspy_posting, adzuna_posting])

    assert len(result) == 1
    assert result[0].source == "adzuna"


def test_dedupe_leaves_distinct_postings_untouched():
    postings = [
        _posting(company="Anthropic", title="Staff Software Engineer"),
        _posting(company="ClickUp", title="Staff Backend Engineer"),
        _posting(company="Inflection AI", title="Principal Engineer"),
    ]

    result = dedupe_cross_source(postings)

    assert len(result) == 3


def test_dedupe_handles_multiple_independent_duplicate_groups():
    postings = [
        _posting(company="Anthropic", title="Staff Software Engineer", source="greenhouse"),
        _posting(company="Anthropic", title="Staff Software Engineer", source="adzuna"),
        _posting(company="ClickUp", title="Staff Backend Engineer", source="ashby"),
        _posting(company="ClickUp", title="Staff Backend Engineer", source="adzuna"),
    ]

    result = dedupe_cross_source(postings)

    assert len(result) == 2
    sources = {p.source for p in result}
    assert sources == {"greenhouse", "ashby"}
