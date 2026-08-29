"""Prompt template loader and renderer.

`load_prompt` returns a `.txt` file verbatim (used for the company-research
prompt, which has no per-search variables). `render_system_prompt` fills the
`${...}` slots in system.txt from a `Profile` — the scoring prompt is
per-candidate, so it is assembled at runtime rather than shipped as a fixed
string.
"""

from __future__ import annotations

from pathlib import Path
from string import Template
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent.profile import Profile

PROMPTS_DIR = Path(__file__).parent


def load_prompt(name: str) -> str:
    """Load a prompt template from a .txt file.

    Args:
        name: Filename without extension (e.g., "system" loads "system.txt").

    Raises:
        FileNotFoundError: If the prompt file does not exist.
    """
    prompt_path = PROMPTS_DIR / f"{name}.txt"
    if not prompt_path.exists():
        raise FileNotFoundError(
            f"Prompt file not found: {prompt_path}. "
            f"Available prompts: {[f.stem for f in PROMPTS_DIR.glob('*.txt')]}"
        )
    return prompt_path.read_text(encoding="utf-8").strip()


def render_prompt(name: str, /, **subs: str) -> str:
    """Load a prompt template and substitute its ``${slot}`` placeholders.

    Uses ``Template.substitute`` (strict): a ``${slot}`` with no matching
    keyword argument raises ``KeyError`` at render time, by design.
    """
    raw = (PROMPTS_DIR / f"{name}.txt").read_text(encoding="utf-8")
    return Template(raw).substitute(**subs).strip()


def _format_usd(amount: int) -> str:
    return f"${amount // 1000}k" if amount % 1000 == 0 else f"${amount:,}"


def render_system_prompt(profile: Profile) -> str:
    """Assemble the Tier 2 scoring system prompt for one candidate profile."""
    bars = profile.comp_bars
    comp_bars = (
        f"{_format_usd(bars.local_or_remote_usd)} for local or fully remote roles. "
        f"{_format_usd(bars.relocation_usd)} if relocation to "
        f"{' / '.join(profile.relocation_cities)} is required. "
        f"{_format_usd(bars.preferred_domain_floor_usd)} for preferred-domain roles "
        f"({', '.join(profile.preferred_domains)}) — this is a hard floor, not a soft "
        "preference; roles below it are rejected even if domain-preferred."
    )
    hard_dealbreakers = f"{', '.join(profile.dealbreakers)}. {profile.dealbreaker_notes.strip()}"

    return render_prompt(
        "system",
        candidate_profile=profile.candidate_summary.strip(),
        seniority_targets=profile.seniority_targets.strip(),
        hard_dealbreakers=hard_dealbreakers,
        comp_bars=comp_bars,
        location_list=", ".join(profile.preferred_locations),
    )
