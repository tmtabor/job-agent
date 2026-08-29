"""LLM-as-judge evals for Tier 2 scoring reasoning quality.

Field-equality checks (evals/test_pass_fail.py) verify *what* the scoring
agent concluded; these verify *why* — the reasoning text actually engages
with the specific judgment call, not just a plausible-sounding restatement.
Run with: uv run pytest -m eval (runs alongside the pass/fail evals)
"""

import json
from pathlib import Path

import pytest

from agent.agents import build_scoring_input, score_posting
from evals.judge import judge_response


def _load_case(name: str) -> dict:
    fixtures = json.loads((Path(__file__).parent / "fixtures" / "jobs.json").read_text())
    return next(f for f in fixtures if f["name"] == name)


@pytest.mark.eval
async def test_crypto_dealbreaker_reasoning_ignores_trading_background(scoring_system_prompt: str):
    """The candidate's earlier trading-systems background must not be treated
    as mitigating the crypto dealbreaker, or as evidence against it. The
    reasoning should reflect that distinction, not just assert the verdict.
    """
    case = _load_case("crypto_trading_adjacent_skill_match")
    scoring_input = build_scoring_input(**case["inputs"])
    output = await score_posting(scoring_input, system_prompt=scoring_system_prompt)

    assert output.dealbreaker is True
    verdict = await judge_response(
        task="Explain why this job posting was flagged as a crypto dealbreaker.",
        response=output.dealbreaker_reasoning or "",
        criteria=(
            "The reasoning should identify the company's cryptocurrency exchange/"
            "trading business as the dealbreaker basis. It must NOT cite the "
            "candidate's earlier low-latency trading-systems background as "
            "relevant to, or mitigating, the crypto dealbreaker — that is "
            "explicitly not cryptocurrency experience and treating skill-match "
            "as evidence either way is a scoring error."
        ),
        threshold=0.7,
    )
    assert verdict.passed, (
        f"Judge score {verdict.score:.2f} below threshold. Reasoning: {verdict.reasoning}"
    )


@pytest.mark.eval
async def test_ambiguous_level_reasoning_cites_scope_language(scoring_system_prompt: str):
    """For an unleveled MTS posting, level_reasoning should infer seniority
    from scope language in the posting, not just note the title is unleveled.
    """
    case = _load_case("unleveled_mts_flat_org")
    scoring_input = build_scoring_input(**case["inputs"])
    output = await score_posting(scoring_input, system_prompt=scoring_system_prompt)

    assert output.level_match == "ambiguous"
    verdict = await judge_response(
        task="Explain the level-match judgment for this unleveled job posting.",
        response=output.level_reasoning,
        criteria=(
            "The reasoning should reference specific scope language from the "
            "posting (e.g. defining technical direction, operating with minimal "
            "oversight) rather than simply stating the title is unleveled or "
            "ambiguous with no supporting detail."
        ),
        threshold=0.6,
    )
    assert verdict.passed, (
        f"Judge score {verdict.score:.2f} below threshold. Reasoning: {verdict.reasoning}"
    )
