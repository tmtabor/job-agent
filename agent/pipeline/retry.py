"""Shared retry/skip wrapper for external calls.

Applied uniformly at the call site for every external dependency (ATS fetch,
Adzuna, Tavily, Gemini, Postmark) by the orchestrator — not baked into each
individual client, so the retry/backoff/skip policy lives in exactly one
place.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from agent.logging import get_logger

logger = get_logger(__name__)

MAX_RETRIES = 3
BASE_DELAY_SECONDS = 1.0


@dataclass
class SkippedUnit:
    """Returned instead of raising when all retries are exhausted.

    The caller should log this and continue — a failed source/company must
    never abort the whole run.
    """

    label: str
    error: str


async def call_with_retry[T](
    label: str,
    func: Callable[[], Awaitable[T]],
    max_retries: int = MAX_RETRIES,
    base_delay_seconds: float = BASE_DELAY_SECONDS,
) -> T | SkippedUnit:
    """Run func() with exponential backoff; return SkippedUnit on exhaustion.

    Args:
        label: Human-readable description of the unit of work, used in logs
            and in the "N sources/postings skipped this run" summary.
        func: Zero-arg async callable to run (bind real arguments with a
            lambda or functools.partial before passing it in).
        max_retries: Total attempts before giving up.
        base_delay_seconds: Backoff base — delay doubles each retry.
    """
    last_error: Exception | None = None

    for attempt in range(1, max_retries + 1):
        try:
            return await func()
        except Exception as e:
            last_error = e
            if attempt < max_retries:
                logger.warning(
                    "External call failed, retrying",
                    extra={
                        "label": label,
                        "attempt": attempt,
                        "max_retries": max_retries,
                        "error": str(e),
                    },
                )
                await asyncio.sleep(base_delay_seconds * (2 ** (attempt - 1)))
            else:
                logger.warning(
                    "External call failed, no retries left",
                    extra={
                        "label": label,
                        "attempt": attempt,
                        "max_retries": max_retries,
                        "error": str(e),
                    },
                )

    logger.error(
        "Skipping unit of work after exhausting retries",
        extra={"label": label, "error": str(last_error)},
    )
    return SkippedUnit(label=label, error=str(last_error))
