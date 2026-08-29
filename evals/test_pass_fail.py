"""Pass/fail evals for Tier 2 scoring, driven by evals/fixtures/jobs.json.

These evals test for specific, verifiable JobEvaluation fields across a
labeled set of representative cases (clear pass, hospital-IT-department AI
role, unleveled MTS, crypto-adjacent dealbreaker, bundled vs. clean comp,
domain-preferred-below-floor, PTO research conflict, generic-role-relabeled).
Every expected verdict is written against profile.example.yaml's comp bars
and dealbreakers.

Run with: uv run pytest -m eval
"""

from dataclasses import dataclass

import pytest
from pydantic_evals import Case, Dataset
from pydantic_evals.evaluators import Evaluator, EvaluatorContext

from agent.agents import build_scoring_input, score_posting
from agent.models import JobEvaluation


@dataclass
class FieldMatch(Evaluator[dict, JobEvaluation]):
    """Pass if every key in `expected_output` matches the JobEvaluation field,
    and every field named in metadata["expected_non_null"] is non-null.
    """

    def evaluate(self, ctx: EvaluatorContext[dict, JobEvaluation]) -> bool:
        output_dict = ctx.output.model_dump()

        expected = ctx.expected_output or {}
        for key, value in expected.items():
            if output_dict.get(key) != value:
                return False

        non_null_fields = (ctx.metadata or {}).get("expected_non_null", [])
        return all(output_dict.get(key) is not None for key in non_null_fields)


@pytest.mark.eval
async def test_scoring_fixture_dataset(job_fixtures: list[dict], scoring_system_prompt: str):
    """Run every case in evals/fixtures/jobs.json through the scoring agent.

    Add cases to that JSON file to grow this eval — no code changes needed
    unless a case requires a new kind of check beyond FieldMatch.
    """
    dataset = Dataset(
        name="job_scoring",
        cases=[
            Case(
                name=fixture["name"],
                inputs=fixture["inputs"],
                expected_output=fixture.get("expected"),
                metadata={"expected_non_null": fixture.get("expected_non_null", [])},
            )
            for fixture in job_fixtures
        ],
        evaluators=[FieldMatch()],
    )

    async def task(inputs: dict) -> JobEvaluation:
        scoring_input = build_scoring_input(**inputs)
        return await score_posting(scoring_input, system_prompt=scoring_system_prompt)

    report = await dataset.evaluate(task)
    report.print(include_input=True, include_output=True)

    averages = report.averages()
    assert averages is not None
    assert averages.assertions == 1.0, "One or more eval cases failed — see the report above."
