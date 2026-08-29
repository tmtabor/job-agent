"""Tier 1 filtering: cheap, no-LLM pass/ambiguous/reject routing.

Runs on every fetched posting, before any LLM call. `reject` postings never
reach Tier 2 scoring; `pass`/`ambiguous` postings do (ambiguous ones get an
explicit role-family or location judgment question in the Tier 2 prompt).

The title / description-keyword / location signals come from the candidate
profile (`profile.tier1`), compiled once per run by `compile_tier1_patterns`.
The guardrail regexes below are structural, not personal, and stay in code.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from agent.models import Posting, Tier1Result

if TYPE_CHECKING:
    from agent.profile import Profile

# Adjacent-but-irrelevant titles that happen to end in "Engineer" — a match
# here means the title does NOT count as a role-family hit, even if it also
# matches one of the profile's title patterns (e.g. a title can't be both
# "Software Engineer" and "Sales Engineer" at once, but this guards near-miss
# phrasing like "Sales Solutions Engineer").
_EXCLUDED_ENGINEER_PREFIX = re.compile(
    r"(?i)\b(sales|field|support|customer|solutions)\s+engineer\b"
)

# Phrases that can hide a geographic restriction on an otherwise-remote
# posting. Only flagged when the clause following the phrase doesn't name the
# US as the qualifying region — "must be authorized to work in the United
# States" is ordinary boilerplate on nearly every US posting and must not
# itself trigger an ambiguous routing.
_RESTRICTION_PHRASE = re.compile(
    r"(?i)(must\s+reside\s+in|must\s+be\s+located\s+in|"
    r"(?:must\s+be\s+)?authorized\s+to\s+work\s+in)\s+(.{0,60})"
)
_US_QUALIFIER = re.compile(r"(?i)\b(u\.?s\.?a?\.?|united\s+states)\b")


@dataclass(frozen=True)
class Tier1Patterns:
    """Compiled Tier 1 signals for one run. Build with compile_tier1_patterns."""

    title: re.Pattern[str]
    description_keywords: re.Pattern[str]
    preferred_locations: re.Pattern[str]


def compile_tier1_patterns(profile: Profile) -> Tier1Patterns:
    """Compile the profile's regex-fragment lists into OR-joined patterns."""

    def _join(fragments: list[str]) -> re.Pattern[str]:
        return re.compile("(?i)(" + "|".join(fragments) + ")")

    return Tier1Patterns(
        title=_join(profile.tier1.title_patterns),
        description_keywords=_join(profile.tier1.description_keywords),
        preferred_locations=_join(profile.tier1.preferred_location_patterns),
    )


def _title_matches(title: str, patterns: Tier1Patterns) -> bool:
    if _EXCLUDED_ENGINEER_PREFIX.search(title):
        return False
    return bool(patterns.title.search(title))


def _description_hits(description: str, patterns: Tier1Patterns) -> bool:
    return bool(patterns.description_keywords.search(description))


def _location_passes(location: str | None, remote: bool, patterns: Tier1Patterns) -> bool:
    if remote:
        return True
    return bool(location and patterns.preferred_locations.search(location))


def has_hidden_location_restriction(description: str) -> bool:
    """True if a restriction phrase appears without a US qualifier nearby."""
    for match in _RESTRICTION_PHRASE.finditer(description):
        clause = match.group(2)
        if not _US_QUALIFIER.search(clause):
            return True
    return False


def tier1_filter(
    posting: Posting,
    dealbreaker_companies: set[str],
    patterns: Tier1Patterns,
) -> Tier1Result:
    """Route one posting to pass/ambiguous/reject without any LLM call.

    Args:
        posting: The normalized posting to evaluate.
        dealbreaker_companies: Lowercased company names from the Tier 1
            blocklist — checked first, short-circuits to reject before any
            regex work.
        patterns: Compiled title/keyword/location signals for this run.
    """
    if posting.company.strip().lower() in dealbreaker_companies:
        return "reject"

    title_hit = _title_matches(posting.title, patterns)
    description_hit = _description_hits(posting.description_text, patterns)

    if not title_hit and not description_hit:
        return "reject"

    if not _location_passes(posting.location, posting.remote, patterns):
        return "reject"

    if has_hidden_location_restriction(posting.description_text):
        return "ambiguous"

    if not title_hit and description_hit:
        return "ambiguous"

    return "pass"
