"""Unit tests for the retry/skip wrapper. Fake failing callables only —
never a real external call."""

import pytest

from agent.pipeline.retry import SkippedUnit, call_with_retry


@pytest.fixture(autouse=True)
def no_real_sleep(monkeypatch):
    """Retries would otherwise really sleep (1s, 2s, ...) — patch it out so
    the test suite stays fast, but still record the delays used.
    """
    delays: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        delays.append(seconds)

    monkeypatch.setattr("agent.pipeline.retry.asyncio.sleep", fake_sleep)
    return delays


async def test_succeeds_on_first_attempt_no_retry(no_real_sleep):
    calls = 0

    async def always_succeeds():
        nonlocal calls
        calls += 1
        return "ok"

    result = await call_with_retry("test-unit", always_succeeds)

    assert result == "ok"
    assert calls == 1
    assert no_real_sleep == []


async def test_succeeds_after_transient_failures(no_real_sleep):
    calls = 0

    async def fails_twice_then_succeeds():
        nonlocal calls
        calls += 1
        if calls < 3:
            raise ConnectionError("transient")
        return "recovered"

    result = await call_with_retry("test-unit", fails_twice_then_succeeds)

    assert result == "recovered"
    assert calls == 3
    assert no_real_sleep == [1.0, 2.0]  # exponential backoff: base * 2**(attempt-1)


async def test_returns_skipped_unit_after_exhausting_retries(no_real_sleep):
    calls = 0

    async def always_fails():
        nonlocal calls
        calls += 1
        raise TimeoutError("never works")

    result = await call_with_retry("test-unit", always_fails, max_retries=3)

    assert isinstance(result, SkippedUnit)
    assert result.label == "test-unit"
    assert "never works" in result.error
    assert calls == 3
    assert no_real_sleep == [1.0, 2.0]  # no sleep after the final attempt


async def test_respects_custom_max_retries_and_base_delay(no_real_sleep):
    calls = 0

    async def always_fails():
        nonlocal calls
        calls += 1
        raise ValueError("nope")

    result = await call_with_retry("test-unit", always_fails, max_retries=2, base_delay_seconds=0.5)

    assert isinstance(result, SkippedUnit)
    assert calls == 2
    assert no_real_sleep == [0.5]
