"""Unit tests for post-processing/bucketing. No LLM, no network."""

from agent.models import JobEvaluation, Posting
from agent.pipeline.post_process import DigestEntry, bucket_entries, is_hard_rejected


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
        "location_reasoning": "Fully remote, no restrictions found.",
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


def test_clean_pass_is_not_hard_rejected():
    assert is_hard_rejected(_evaluation()) is False


def test_dealbreaker_is_hard_rejected():
    assert is_hard_rejected(_evaluation(dealbreaker=True)) is True


def test_comp_no_is_hard_rejected():
    assert is_hard_rejected(_evaluation(comp_meets_bar="no")) is True


def test_location_mismatch_is_hard_rejected():
    assert is_hard_rejected(_evaluation(location_match=False)) is True


def test_level_no_is_hard_rejected():
    assert is_hard_rejected(_evaluation(level_match="no")) is True


def test_unstated_comp_and_ambiguous_level_are_not_hard_rejects():
    assert is_hard_rejected(_evaluation(comp_meets_bar="unstated")) is False
    assert is_hard_rejected(_evaluation(level_match="ambiguous")) is False


def test_hard_rejected_entries_excluded_from_all_buckets():
    entries = [_entry(dealbreaker=True), _entry(comp_meets_bar="no")]
    result = bucket_entries(entries)
    assert result.main == []
    assert result.unstated_comp == []
    assert result.ambiguous_level == []


def test_clean_survivor_goes_to_main():
    entries = [_entry()]
    result = bucket_entries(entries)
    assert len(result.main) == 1
    assert result.unstated_comp == []
    assert result.ambiguous_level == []


def test_unstated_comp_survivor_goes_to_unstated_comp_bucket():
    entries = [_entry(comp_meets_bar="unstated", stated_comp=None)]
    result = bucket_entries(entries)
    assert result.main == []
    assert len(result.unstated_comp) == 1


def test_ambiguous_level_survivor_goes_to_ambiguous_level_bucket():
    entries = [_entry(level_match="ambiguous")]
    result = bucket_entries(entries)
    assert result.main == []
    assert len(result.ambiguous_level) == 1


def test_unstated_comp_takes_precedence_over_ambiguous_level():
    entries = [_entry(comp_meets_bar="unstated", stated_comp=None, level_match="ambiguous")]
    result = bucket_entries(entries)
    assert len(result.unstated_comp) == 1
    assert result.ambiguous_level == []


def test_buckets_sorted_by_fit_score_descending():
    entries = [
        _entry(overall_fit_score=5),
        _entry(overall_fit_score=9),
        _entry(overall_fit_score=7),
    ]
    result = bucket_entries(entries)
    scores = [e.evaluation.overall_fit_score for e in result.main]
    assert scores == [9, 7, 5]


def test_research_conflict_preserved_regardless_of_bucket():
    entries = [
        _entry(company_research_conflict="posting says flexible PTO; research disagrees"),
        _entry(
            comp_meets_bar="unstated",
            stated_comp=None,
            company_research_conflict="another conflict",
        ),
    ]
    result = bucket_entries(entries)
    assert result.main[0].evaluation.company_research_conflict is not None
    assert result.unstated_comp[0].evaluation.company_research_conflict is not None
