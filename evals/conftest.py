"""Pytest fixtures for eval runs."""

import json
from pathlib import Path

import pytest

from agent.logging import configure_logging
from agent.profile import Profile, load_profile
from agent.prompts.templates import render_system_prompt

EXAMPLE_PROFILE_PATH = Path(__file__).resolve().parent.parent / "profile.example.yaml"


@pytest.fixture(scope="session", autouse=True)
def setup_logging():
    """Configure logging once for the eval session."""
    configure_logging()


@pytest.fixture(scope="session")
def example_profile() -> Profile:
    """The fictional candidate profile the eval fixtures are written against."""
    return load_profile(EXAMPLE_PROFILE_PATH)


@pytest.fixture(scope="session")
def scoring_system_prompt(example_profile: Profile) -> str:
    """The Tier 2 scoring prompt rendered from the example profile."""
    return render_system_prompt(example_profile)


@pytest.fixture
def job_fixtures() -> list[dict]:
    """Load the labeled job-posting eval fixtures from JSON."""
    fixtures_path = Path(__file__).parent / "fixtures" / "jobs.json"
    return json.loads(fixtures_path.read_text())
