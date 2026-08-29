"""Unit tests for Tier 1 filtering. No LLM calls, no network.

Patterns are compiled from profile.example.yaml, so the location assertions
below (San Diego / Palo Alto pass, Omaha rejects) bind to that file's
tier1.preferred_location_patterns.
"""

from pathlib import Path

from agent.models import Posting
from agent.pipeline.tier1 import (
    compile_tier1_patterns,
    has_hidden_location_restriction,
    tier1_filter,
)
from agent.profile import load_profile

_PATTERNS = compile_tier1_patterns(
    load_profile(Path(__file__).resolve().parent.parent / "profile.example.yaml")
)


def _filter(posting: Posting, dealbreaker_companies: set[str] | None = None) -> str:
    return tier1_filter(posting, dealbreaker_companies or set(), _PATTERNS)


def _posting(**overrides) -> Posting:
    defaults = {
        "source": "greenhouse",
        "source_native_id": "123",
        "company": "Example Corp",
        "title": "Staff Software Engineer",
        "location": "Remote",
        "remote": True,
        "department": "Engineering",
        "description_text": "Build our agentic AI platform using Python and RAG.",
        "compensation_text": None,
        "apply_url": "https://example.com/jobs/123",
    }
    defaults.update(overrides)
    return Posting(**defaults)


def test_title_match_remote_passes():
    posting = _posting(title="Staff Software Engineer", remote=True)
    assert _filter(posting) == "pass"


def test_title_miss_description_hit_is_ambiguous():
    posting = _posting(
        title="Backend Developer",
        description_text="You'll build RAG pipelines and LLM agent orchestration in Python.",
    )
    assert _filter(posting) == "ambiguous"


def test_generic_agent_mention_in_boilerplate_does_not_trigger_ambiguous():
    """Regression test: a company whose product IS AI agents will mention
    "agent(s)" in nearly every posting's About-Us boilerplate, including
    clearly non-technical roles. Bare "agent"/"llm"/"agentic" mentions alone
    must not trigger the description backstop — confirmed against a large AI
    company's live board 2026-07-24 (Legal Program Manager, Sales Account
    Executive, etc. were all being routed to `ambiguous` this way).
    """
    posting = _posting(
        title="Customer Success Programs Manager",
        description_text=(
            "As a CS Programs Lead you'll think 'how could we do this with "
            "an agent?' — your default is to build an agent before a manual "
            "process. We are hiring across the org to scale adoption of our "
            "agentic products with enterprise customers."
        ),
    )
    assert _filter(posting) == "reject"


def test_neither_title_nor_description_rejects():
    posting = _posting(
        title="Warehouse Associate",
        description_text="Lift boxes and operate a forklift.",
    )
    assert _filter(posting) == "reject"


def test_dealbreaker_blocklist_rejects_before_anything_else():
    posting = _posting(title="Staff Software Engineer", company="Blocked Co")
    assert _filter(posting, {"blocked co"}) == "reject"


def test_dealbreaker_blocklist_is_case_insensitive():
    posting = _posting(company="Blocked CO")
    assert _filter(posting, {"blocked co"}) == "reject"


def test_location_fails_when_not_remote_and_not_preferred_city():
    posting = _posting(remote=False, location="Omaha, NE")
    assert _filter(posting) == "reject"


def test_location_passes_for_preferred_onsite_city():
    posting = _posting(remote=False, location="San Diego, CA")
    assert _filter(posting) == "pass"


def test_location_passes_for_palo_alto():
    """Regression test: a real posting ("Staff Engineer, Agentic", Palo Alto)
    was wrongly hard-rejected 2026-07-24 because Palo Alto — part of the Bay
    Area — wasn't in the preferred-location list, even though remote=False and
    the title matched cleanly otherwise.
    """
    posting = _posting(
        title="Staff Engineer, Agentic",
        remote=False,
        location="Palo Alto, California, United States",
    )
    assert _filter(posting) == "pass"


def test_hidden_restriction_routes_to_ambiguous_not_reject():
    posting = _posting(
        remote=True,
        description_text=(
            "Build our agentic AI platform using Python and RAG. Must reside in Ontario, Canada."
        ),
    )
    assert _filter(posting) == "ambiguous"


def test_us_authorization_boilerplate_does_not_trigger_ambiguous():
    posting = _posting(
        remote=True,
        description_text=(
            "Build our agentic AI platform using Python and RAG. "
            "Must be authorized to work in the United States."
        ),
    )
    assert _filter(posting) == "pass"


def test_excluded_engineer_title_does_not_count_as_title_hit():
    posting = _posting(
        title="Solutions Engineer",
        description_text="Help customers configure our dashboard. No coding required.",
    )
    assert _filter(posting) == "reject"


def test_excluded_engineer_title_with_description_hit_is_ambiguous():
    posting = _posting(
        title="Solutions Engineer",
        description_text="You'll write Python and build RAG-based agent demos for customers.",
    )
    assert _filter(posting) == "ambiguous"


def test_has_hidden_location_restriction_direct():
    assert has_hidden_location_restriction("Must reside in the EU.")
    assert not has_hidden_location_restriction("Must be authorized to work in the U.S.")
    assert not has_hidden_location_restriction("No restrictions mentioned here.")
