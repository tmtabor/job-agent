"""Company research agent: synthesizes Tavily search results into structured
research.

Deliberately not a tool-calling agent: Tavily search runs as a plain,
separate step before the LLM call, with raw results fed in as text context —
not the model's native Search grounding tool (paid-tier only on Gemini). So
this agent takes pre-fetched search results as input text and does one
structured-output call, same "single-shot" shape as the scoring agent in
agent/agents/single.py.

Covered by tests/conftest.py's autouse TestModel override because that
fixture scans every module name starting with "agent.agents" already
imported into sys.modules — this module just needs to actually be imported
by anything that touches it (see the pre-import list there).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pydantic_ai import Agent
from pydantic_ai.usage import RunUsage, UsageLimits

from agent.config import settings
from agent.logging import configure_logging, get_logger
from agent.models import CompanyResearchOutput
from agent.prompts.templates import load_prompt

logger = get_logger(__name__)

USAGE_LIMITS = UsageLimits(request_limit=4, total_tokens_limit=20_000)


@dataclass
class CompanyResearchDeps:
    """No runtime dependencies needed — all context is in the prompt text."""


company_research_agent: Agent[CompanyResearchDeps, CompanyResearchOutput] = Agent(
    settings.model,
    name="company_research_agent",
    output_type=CompanyResearchOutput,
    deps_type=CompanyResearchDeps,
    instructions=load_prompt("company_research"),
)


#: Per-result cap on how much of raw_content gets into the prompt. Verified
#: live 2026-07-24 that some pages (e.g. levels.fyi benefit comparison
#: tables) return 30k+ chars of raw_content — with up to
#: COMPANY_RESEARCH_MAX_RESULTS results per call, uncapped this could blow
#: through company_research_agent's USAGE_LIMITS.total_tokens_limit on its
#: own. 2,500 chars leaves headroom for 8 results within that budget.
_RAW_CONTENT_CHAR_LIMIT = 2500

#: Research-relevant keyword GROUPS used to anchor excerpt windows (see
#: _relevant_excerpt) — one independent window per group, not one
#: winner-take-all match across all of them. Verified live 2026-07-24 that a
#: single-anchor version (one match across all keywords combined) failed:
#: whichever topic happened to be mentioned earliest in a page — "remote-
#: first" at character 1348 in one real example — won the anchor and
#: crowded out a later, more specific PTO mention ("flexible PTO",
#: attributed directly to the company) at character 7756 of the same page.
#: Giving each topic its own budgeted window means an early RTO mention no
#: longer starves out a later PTO mention in the same source. Deliberately
#: excludes generic words like "benefit(s)" — verified those match almost
#: immediately in nearly any company-review article (e.g. an "at a glance:
#: Benefit Rating: TBD" summary near the top) without being real evidence.
_TOPIC_KEYWORD_GROUPS = [
    re.compile(r"(?i)\b(pto|vacation|time\s*off|unlimited|days?\s*off|weeks?\s*off)\b"),
    re.compile(r"(?i)\b(layoff|hiring\s*freeze)\b"),
    re.compile(r"(?i)\b(return\s*to\s*office|\brto\b|remote[- ]first)\b"),
]


def _relevant_excerpt(text: str, char_limit: int) -> str:
    """Keyword-anchored excerpts, not a blind first-N-chars truncation.

    One independent window per _TOPIC_KEYWORD_GROUPS entry (each getting an
    equal share of char_limit), so a company's whole raw_content doesn't
    have to be read to get evidence for more than one research topic out of
    a single source. Falls back to a plain start-of-text truncation when no
    keyword from any group appears at all.
    """
    if len(text) <= char_limit:
        return text

    per_topic_budget = char_limit // len(_TOPIC_KEYWORD_GROUPS)
    lead_in = 200
    windows = []
    for pattern in _TOPIC_KEYWORD_GROUPS:
        match = pattern.search(text)
        if match is not None:
            start = max(0, match.start() - lead_in)
            windows.append(text[start : start + per_topic_budget])

    if not windows:
        return text[:char_limit]
    return "\n[...]\n".join(windows)


class SearchResult:
    """A single Tavily result — kept minimal, not the full Tavily response shape."""

    def __init__(self, title: str, url: str, content: str, raw_content: str | None = None) -> None:
        self.title = title
        self.url = url
        self.content = content
        self.raw_content = raw_content


def build_research_input(company_name: str, search_results: list[SearchResult]) -> str:
    """Assemble the user-turn text for one company-research call.

    Prefers each result's raw_content over its short content snippet when
    available — verified live 2026-07-24 that the short snippet is often
    too thin to carry real policy detail (e.g. a PTO page's snippet
    confirming only a star rating, never the actual policy), even from a
    well-targeted query and directly relevant URL.
    """
    lines = [f"COMPANY: {company_name}", "", "SEARCH RESULTS:"]
    if not search_results:
        lines.append("(no search results returned)")
    for i, result in enumerate(search_results, start=1):
        body = result.raw_content or result.content
        lines.extend(
            [
                f"\n[{i}] {result.title}",
                f"URL: {result.url}",
                f"CONTENT: {_relevant_excerpt(body, _RAW_CONTENT_CHAR_LIMIT)}",
            ]
        )
    return "\n".join(lines)


async def research_company(
    research_input: str,
    deps: CompanyResearchDeps | None = None,
    usage: RunUsage | None = None,
) -> CompanyResearchOutput:
    """Run the company research agent on one assembled input.

    Args:
        research_input: Output of build_research_input().
        deps: Runtime dependencies. Created with defaults if not provided.
        usage: Optional accumulator to add this call's token usage into —
            lets run_pipeline.py total up a run's LLM cost without changing
            this function's return type.

    Returns:
        Validated CompanyResearchOutput instance.
    """
    if deps is None:
        deps = CompanyResearchDeps()

    logger.info("Researching company")
    result = await company_research_agent.run(research_input, deps=deps, usage_limits=USAGE_LIMITS)
    logger.info("Company research complete")
    if usage is not None:
        usage.incr(result.usage)
    return result.output


if __name__ == "__main__":
    import asyncio

    configure_logging()
    example_input = build_research_input(
        "Example Corp",
        [SearchResult("Example Corp raises Series C", "https://example.com/a", "...")],
    )
    output = asyncio.run(research_company(example_input))
    print(output.model_dump_json(indent=2))
