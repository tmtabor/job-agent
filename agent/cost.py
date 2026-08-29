"""LLM inference cost estimation for the run-scope summary (agent/digest.py).

Uses genai_prices (a pydantic-ai dependency already vendored for its own
internal cost tracking) rather than hand-maintained per-model rates, so a
model swap in .env doesn't silently make this estimate stale.
"""

from __future__ import annotations

from decimal import Decimal

from genai_prices import calc_price
from pydantic_ai.usage import RunUsage

from agent.config import settings


def estimate_cost_usd(*usages: RunUsage) -> Decimal:
    """Sum genai_prices' cost estimate for one or more usage accumulators.

    Both the scoring agent (agent/agents/single.py) and the company-research
    agent (agent/agents/company_research.py) run on the same configured
    AGENT_MODEL, so a single provider/model split applies to every usage
    accumulator passed in.
    """
    provider, model_ref = settings.model.split(":", 1)
    total = Decimal("0")
    for usage in usages:
        if usage.input_tokens == 0 and usage.output_tokens == 0:
            continue
        total += calc_price(usage, model_ref, provider_id=provider).total_price
    return total
