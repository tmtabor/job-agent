"""Tier 2 scoring agent: single-shot structured evaluation of one posting.

Single-shot because the control flow between pipeline stages (when to fetch
company research, when to score, when to persist) is decided by plain Python
in scripts/run_pipeline.py, not by the LLM — there is nothing here for the
model to dynamically delegate or loop over.

The system prompt is per-candidate (rendered from a Profile), so it is passed
in on `AgentDeps` and returned by a dynamic `@agent.instructions` function
rather than baked in at construction time.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic_ai import Agent, RunContext
from pydantic_ai.usage import RunUsage, UsageLimits

from agent.config import settings
from agent.logging import configure_logging, get_logger
from agent.models import JobEvaluation

logger = get_logger(__name__)

# Tier 1 filtering upstream is what keeps call volume (and cost) manageable,
# not a big request_limit here. One posting per call, no tool loop, so this
# only needs to cover the single round trip plus a validation retry or two.
USAGE_LIMITS = UsageLimits(request_limit=4, total_tokens_limit=20_000)


@dataclass
class AgentDeps:
    """Per-run scoring context. `system_prompt` is rendered from a Profile by
    the caller (see agent.prompts.templates.render_system_prompt)."""

    system_prompt: str


agent: Agent[AgentDeps, JobEvaluation] = Agent(
    settings.model,
    name="scoring_agent",
    output_type=JobEvaluation,
    deps_type=AgentDeps,
)


@agent.instructions
def _system_prompt(ctx: RunContext[AgentDeps]) -> str:
    return ctx.deps.system_prompt


def build_scoring_input(
    posting_title: str,
    posting_company: str,
    posting_location: str | None,
    posting_remote: bool,
    posting_description: str,
    posting_compensation: str | None,
    company_research_text: str | None,
    tier1_result: str,
) -> str:
    """Assemble the user-turn text for one scoring call.

    Kept as a plain function (not baked into score_posting) so tests/evals can
    construct exactly the input they want without going through a Posting
    instance.
    """
    lines = [
        f"TITLE: {posting_title}",
        f"COMPANY: {posting_company}",
        f"LOCATION: {posting_location or 'not specified'}",
        f"REMOTE: {posting_remote}",
        f"TIER1_RESULT: {tier1_result}",
        f"STATED COMPENSATION: {posting_compensation or 'not stated in posting'}",
        "",
        "POSTING DESCRIPTION:",
        posting_description,
        "",
        "COMPANY RESEARCH:",
        company_research_text or "No company research available for this company.",
    ]
    return "\n".join(lines)


async def score_posting(
    scoring_input: str,
    *,
    system_prompt: str,
    usage: RunUsage | None = None,
) -> JobEvaluation:
    """Run the scoring agent on one assembled posting+research input.

    Args:
        scoring_input: Output of build_scoring_input() (or an eval fixture
            string built the same way).
        system_prompt: Rendered scoring prompt for the candidate profile
            (agent.prompts.templates.render_system_prompt).
        usage: Optional accumulator to add this call's token usage into —
            lets run_pipeline.py total up a run's LLM cost without changing
            this function's return type (evals/tests call this expecting a
            bare JobEvaluation back).

    Returns:
        Validated JobEvaluation instance.
    """
    logger.info("Scoring posting")
    result = await agent.run(
        scoring_input, deps=AgentDeps(system_prompt=system_prompt), usage_limits=USAGE_LIMITS
    )
    logger.info("Scoring complete", extra={"overall_fit_score": result.output.overall_fit_score})
    if usage is not None:
        usage.incr(result.usage)
    return result.output


if __name__ == "__main__":
    import asyncio

    from agent.profile import load_profile
    from agent.prompts.templates import render_system_prompt

    configure_logging()
    example_input = build_scoring_input(
        posting_title="Staff Software Engineer, Agentic Platform",
        posting_company="Example Corp",
        posting_location="Remote",
        posting_remote=True,
        posting_description="Build and operate our multi-agent LLM orchestration platform.",
        posting_compensation="$220,000 - $260,000",
        company_research_text=None,
        tier1_result="pass",
    )
    output = asyncio.run(
        score_posting(example_input, system_prompt=render_system_prompt(load_profile()))
    )
    print(output.model_dump_json(indent=2))
