"""Pytest fixtures for unit tests."""

import os

# Unit tests must run with no real credentials and no API calls. Settings
# requires GOOGLE_API_KEY for the selected model at import time, and the
# module-level Agent construction may create a provider client that also
# wants a key — set a dummy value before anything under agent/ is imported.
# setdefault() leaves a real key untouched if one is present.
os.environ.setdefault("GOOGLE_API_KEY", "unit-test-dummy-key")

import importlib  # noqa: E402
import sys  # noqa: E402
from contextlib import ExitStack, suppress  # noqa: E402

import pytest  # noqa: E402
from pydantic_ai import Agent  # noqa: E402
from pydantic_ai.models.test import TestModel  # noqa: E402

from agent.logging import configure_logging  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def setup_logging():
    """Configure logging once for the test session."""
    configure_logging()


@pytest.fixture(autouse=True)
def override_all_agents_with_test_model():
    """Safety net: no unit test may ever hit a real model API.

    Overrides every Agent defined under agent.agents — including nested
    worker agents that tools delegate to — with TestModel. Tests can still
    apply their own override on top; the innermost override wins.

    The agent modules are pre-imported here so the override also covers
    modules a test imports lazily in its body — otherwise a module first
    imported mid-test would escape the net.
    """
    for mod in ("single", "company_research"):
        with suppress(ModuleNotFoundError):
            importlib.import_module(f"agent.agents.{mod}")

    with ExitStack() as stack:
        for name, module in list(sys.modules.items()):
            if name.startswith("agent.agents") and module is not None:
                for value in vars(module).values():
                    if isinstance(value, Agent):
                        stack.enter_context(value.override(model=TestModel()))
        yield
