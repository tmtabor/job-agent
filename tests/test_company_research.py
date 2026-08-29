"""Unit tests for agent/agents/company_research.py's content-extraction
logic. No LLM calls, no network — pure functions over fixture text."""

from agent.agents.company_research import SearchResult, _relevant_excerpt, build_research_input


def test_relevant_excerpt_returns_text_unchanged_when_under_limit():
    text = "Short text with PTO mentioned."
    assert _relevant_excerpt(text, char_limit=1000) == text


def test_relevant_excerpt_falls_back_to_start_when_no_keyword_present():
    text = "x" * 5000  # no research keyword anywhere
    result = _relevant_excerpt(text, char_limit=500)
    assert result == text[:500]


def test_relevant_excerpt_finds_pto_mention_buried_deep_in_long_text():
    """Regression test for a real bug (2026-07-24): a getbridged.co review
    page had its actual PTO evidence ("flexible PTO", attributed to
    Anthropic) at character 7756 of a 22,053-char page. A naive first-N-
    chars truncation silently cut it off before it ever reached the model,
    which is why pto_type kept coming back "unspecified" despite the right
    URL being in the result set.
    """
    filler = "Lorem ipsum filler text about the company culture. " * 150
    text = filler + "Anthropic offers flexible PTO to all employees." + filler
    assert len(text) > 10000

    excerpt = _relevant_excerpt(text, char_limit=2500)
    assert "flexible PTO" in excerpt


def test_relevant_excerpt_does_not_let_an_earlier_topic_starve_a_later_one():
    """The core regression: an early RTO/remote-first mention must not
    crowd out a later PTO mention from the same source — verified live
    that a single-anchor version failed this exact scenario ("remote-first"
    at character 1348 winning the anchor, "flexible PTO" at 7756 never
    making it into the excerpt at all).
    """
    early_rto_mention = "The company maintains a remote-first culture. "
    filler = "Generic company overview text goes here. " * 200
    late_pto_mention = "Anthropic offers flexible PTO to all staff."
    text = early_rto_mention + filler + late_pto_mention
    assert len(text) > 5000

    excerpt = _relevant_excerpt(text, char_limit=2500)
    assert "remote-first" in excerpt
    assert "flexible PTO" in excerpt


def test_relevant_excerpt_finds_layoff_mention():
    filler = "Unrelated company overview text. " * 200
    text = filler + "The company announced a hiring freeze in Q2." + filler
    excerpt = _relevant_excerpt(text, char_limit=2500)
    assert "hiring freeze" in excerpt


def test_build_research_input_prefers_raw_content_over_short_snippet():
    results = [
        SearchResult(
            title="Example benefits page",
            url="https://example.com/benefits",
            content="Great benefits, 4.6 stars.",
            raw_content="The full page states: Anthropic offers 15 days PTO annually.",
        )
    ]
    text = build_research_input("Anthropic", results)
    assert "15 days PTO annually" in text
    assert "Great benefits, 4.6 stars" not in text


def test_build_research_input_falls_back_to_short_snippet_when_raw_content_missing():
    """E.g. Glassdoor, which blocks raw-content extraction entirely and
    returns an empty/None raw_content even when the URL itself is relevant.
    """
    results = [
        SearchResult(
            title="Glassdoor reviews",
            url="https://www.glassdoor.com/Reviews/Example",
            content="Example Co employees rate benefits 4.6/5.",
            raw_content=None,
        )
    ]
    text = build_research_input("Example Co", results)
    assert "Example Co employees rate benefits 4.6/5." in text


def test_build_research_input_handles_no_results():
    text = build_research_input("Example Co", [])
    assert "no search results returned" in text
