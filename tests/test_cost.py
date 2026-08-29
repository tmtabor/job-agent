"""Unit tests for LLM cost estimation (agent/cost.py). No network calls —
genai_prices.calc_price looks up its bundled pricing snapshot locally.
"""

from decimal import Decimal

from genai_prices import calc_price
from pydantic_ai.usage import RunUsage

from agent.config import settings
from agent.cost import estimate_cost_usd


def test_zero_usage_is_zero_cost():
    assert estimate_cost_usd(RunUsage()) == Decimal("0")


def test_sums_usage_across_multiple_accumulators():
    usage_a = RunUsage(input_tokens=1000, output_tokens=200)
    usage_b = RunUsage(input_tokens=500, output_tokens=100)

    provider, model_ref = settings.model.split(":", 1)
    expected = (
        calc_price(usage_a, model_ref, provider_id=provider).total_price
        + calc_price(usage_b, model_ref, provider_id=provider).total_price
    )

    assert estimate_cost_usd(usage_a, usage_b) == expected


def test_empty_usage_among_others_contributes_nothing():
    usage = RunUsage(input_tokens=1000, output_tokens=200)
    assert estimate_cost_usd(usage, RunUsage()) == estimate_cost_usd(usage)
