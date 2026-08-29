"""Unit tests for digest HTML rendering. Structural checks only — not exact
byte-for-byte HTML."""

from decimal import Decimal

from agent.digest import RunStats, build_digest_html, total_match_count
from agent.models import JobEvaluation, Posting
from agent.pipeline.post_process import BucketedDigest, DigestEntry


def _posting(**overrides) -> Posting:
    defaults = {
        "source": "greenhouse",
        "source_native_id": "1",
        "company": "Example Corp",
        "title": "Staff Software Engineer",
        "location": "Remote",
        "remote": True,
        "department": "Engineering",
        "description_text": "Build things.",
        "compensation_text": "$320,000",
        "apply_url": "https://example.com/jobs/1",
    }
    defaults.update(overrides)
    return Posting(**defaults)


def _evaluation(**overrides) -> JobEvaluation:
    defaults = {
        "role_family_match": "yes",
        "location_match": True,
        "location_reasoning": "Fully remote.",
        "comp_meets_bar": "yes",
        "stated_comp": "$320,000",
        "comp_bar_used": "local_or_remote",
        "level_match": "yes",
        "level_reasoning": "Explicit Staff title.",
        "wlb_signal": "neutral",
        "wlb_evidence": "No signals either way.",
        "pto_type": "standard_accrued",
        "pto_evidence": "Standard PTO policy stated.",
        "domain": "neutral",
        "domain_reasoning": "General SaaS company.",
        "dealbreaker": False,
        "dealbreaker_reasoning": None,
        "ai_title_substance": "not_applicable",
        "scope_ic_vs_management": "ic",
        "remote_specifics": "US timezones, no travel required.",
        "company_research_conflict": None,
        "overall_fit_score": 8,
        "summary": "Strong fit.",
    }
    defaults.update(overrides)
    return JobEvaluation(**defaults)


def _entry(**overrides) -> DigestEntry:
    return DigestEntry(posting=_posting(), evaluation=_evaluation(**overrides))


def test_empty_digest_shows_fallback_message():
    html = build_digest_html(BucketedDigest(main=[], unstated_comp=[], ambiguous_level=[]))
    assert "No postings passed scoring today" in html


def test_main_section_renders_entry_details():
    entry = _entry()
    html = build_digest_html(BucketedDigest(main=[entry], unstated_comp=[], ambiguous_level=[]))

    assert "Top matches" in html
    assert "Staff Software Engineer" in html
    assert "Example Corp" in html
    assert "$320,000" in html
    assert "8/10" in html
    assert 'href="https://example.com/jobs/1"' in html
    assert "Unstated compensation" not in html
    assert "Ambiguous level" not in html


def test_unstated_comp_section_only_present_when_populated():
    entry = _entry(comp_meets_bar="unstated", stated_comp=None)
    html = build_digest_html(BucketedDigest(main=[], unstated_comp=[entry], ambiguous_level=[]))

    assert "Unstated compensation" in html
    assert "not listed" in html
    assert "Top matches" not in html


def test_ambiguous_level_section_shows_reasoning():
    entry = _entry(
        level_match="ambiguous", level_reasoning="Unleveled MTS title, scope suggests staff."
    )
    html = build_digest_html(BucketedDigest(main=[], unstated_comp=[], ambiguous_level=[entry]))

    assert "Ambiguous level" in html
    assert "Unleveled MTS title, scope suggests staff." in html


def test_research_conflict_shown_inline():
    entry = _entry(company_research_conflict="posting says flexible PTO; research disagrees")
    html = build_digest_html(BucketedDigest(main=[entry], unstated_comp=[], ambiguous_level=[]))

    assert "Research conflict" in html
    assert "posting says flexible PTO; research disagrees" in html


def test_skipped_summary_rendered_in_footer_when_provided():
    html = build_digest_html(
        BucketedDigest(main=[], unstated_comp=[], ambiguous_level=[]),
        skipped_summary="2 sources skipped this run",
    )
    assert "2 sources skipped this run" in html


def test_skipped_summary_absent_when_not_provided():
    html = build_digest_html(BucketedDigest(main=[], unstated_comp=[], ambiguous_level=[]))
    assert "<footer>" not in html


def test_malicious_posting_content_is_html_escaped():
    entry = _entry()
    entry.posting.title = "<script>alert(1)</script>"
    html = build_digest_html(BucketedDigest(main=[entry], unstated_comp=[], ambiguous_level=[]))

    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_company_breakdown_alphabetical_with_counts():
    entries = [
        _entry(),  # Example Corp
        DigestEntry(posting=_posting(company="Anthropic"), evaluation=_evaluation()),
        DigestEntry(posting=_posting(company="Anthropic"), evaluation=_evaluation()),
        DigestEntry(posting=_posting(company="ClickUp"), evaluation=_evaluation()),
    ]
    html = build_digest_html(BucketedDigest(main=entries, unstated_comp=[], ambiguous_level=[]))

    assert "Matches by company" in html
    assert "Anthropic (2)" in html
    assert "ClickUp (1)" in html
    assert "Example Corp (1)" in html
    # alphabetical: Anthropic before ClickUp before Example Corp
    assert html.index("Anthropic (2)") < html.index("ClickUp (1)") < html.index("Example Corp (1)")


def test_company_breakdown_counts_across_all_buckets():
    main_entry = DigestEntry(posting=_posting(company="Anthropic"), evaluation=_evaluation())
    unstated_entry = DigestEntry(
        posting=_posting(company="Anthropic"),
        evaluation=_evaluation(comp_meets_bar="unstated", stated_comp=None),
    )
    html = build_digest_html(
        BucketedDigest(main=[main_entry], unstated_comp=[unstated_entry], ambiguous_level=[])
    )

    assert "Anthropic (2)" in html


def test_company_breakdown_absent_when_no_entries():
    html = build_digest_html(BucketedDigest(main=[], unstated_comp=[], ambiguous_level=[]))
    assert "Matches by company" not in html


def test_pto_days_estimate_shown_when_present():
    entry = _entry(pto_days_estimate=20)
    html = build_digest_html(BucketedDigest(main=[entry], unstated_comp=[], ambiguous_level=[]))
    assert "20 days PTO" in html


def test_pto_category_shown_when_no_days_estimate():
    entry = _entry(pto_type="flex_unlimited", pto_days_estimate=None)
    html = build_digest_html(BucketedDigest(main=[entry], unstated_comp=[], ambiguous_level=[]))
    assert "Unlimited/flexible PTO" in html
    assert "None days PTO" not in html


def test_total_match_count_sums_all_buckets():
    bucketed = BucketedDigest(
        main=[_entry(), _entry()],
        unstated_comp=[_entry(comp_meets_bar="unstated", stated_comp=None)],
        ambiguous_level=[_entry(level_match="ambiguous")],
    )
    assert total_match_count(bucketed) == 4


def test_total_match_count_zero_for_empty_digest():
    assert total_match_count(BucketedDigest(main=[], unstated_comp=[], ambiguous_level=[])) == 0


def _stats(**overrides) -> RunStats:
    defaults = {
        "companies_researched": 3,
        "new_roles_found": 42,
        "eliminated_by_prefilter": 20,
        "eliminated_by_llm": 15,
        "remaining": 7,
        "estimated_cost_usd": Decimal("0.0123"),
    }
    defaults.update(overrides)
    return RunStats(**defaults)


def test_scope_section_absent_when_stats_not_provided():
    html = build_digest_html(BucketedDigest(main=[], unstated_comp=[], ambiguous_level=[]))
    assert "Run scope" not in html


def test_scope_section_renders_all_counters():
    html = build_digest_html(
        BucketedDigest(main=[], unstated_comp=[], ambiguous_level=[]), stats=_stats()
    )

    assert "Run scope" in html
    assert "Companies researched" in html and "3" in html
    assert "New roles found" in html and "42" in html
    assert "Eliminated by pre-filtering" in html and "20" in html
    assert "Eliminated by LLM evaluation" in html and "15" in html
    assert "Roles remaining" in html and "7" in html
    assert "$0.0123" in html


def test_scope_section_appears_before_company_breakdown():
    entry = _entry()
    html = build_digest_html(
        BucketedDigest(main=[entry], unstated_comp=[], ambiguous_level=[]), stats=_stats()
    )
    assert html.index("Run scope") < html.index("Matches by company")
