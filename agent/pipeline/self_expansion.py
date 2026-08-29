"""Company self-expansion.

Greenhouse/Lever/Ashby have no discovery endpoint — there's no way to
enumerate "every company on Greenhouse." Instead: whenever a discovery
source (Adzuna or JobSpy) surfaces a company not already tracked, probe the
standard board-URL pattern for each ATS to see if that company also has a
direct board, and if so, hand it back to the caller to record for future
runs.

Deliberately does not use agent.pipeline.retry.call_with_retry: a 404 here
is an expected, common outcome (most companies don't have a public
Greenhouse/Lever/Ashby board), not a failure worth retrying or logging as
skipped work.
"""

from __future__ import annotations

import re

import httpx

from agent.sources.ashby import fetch_ashby_raw
from agent.sources.greenhouse import fetch_greenhouse_raw
from agent.sources.lever import fetch_lever_raw

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def candidate_slugs(company_name: str) -> list[str]:
    """Plausible board-URL slugs for a company name.

    Verified conventions (2026-07-24, real boards): "Anthropic" ->
    "anthropic", "Inflection AI" -> "inflectionai", "ClickUp" -> "clickup" —
    lowercase, all non-alphanumeric characters stripped, no separator. A
    hyphenated variant is included as a secondary guess since it's also a
    common convention elsewhere.
    """
    lowered = company_name.lower().strip()
    no_separator = _NON_ALNUM.sub("", lowered)
    hyphenated = re.sub(r"\s+", "-", re.sub(r"[^a-z0-9\s]", "", lowered)).strip("-")

    candidates = [no_separator]
    if hyphenated and hyphenated != no_separator:
        candidates.append(hyphenated)
    return candidates


def _company_name_matches(target: str, candidate: str | None) -> bool:
    if not candidate:
        return False
    return _NON_ALNUM.sub("", target.lower()) == _NON_ALNUM.sub("", candidate.lower())


async def _probe_greenhouse(client: httpx.AsyncClient, company_name: str, slug: str) -> bool:
    try:
        raw = await fetch_greenhouse_raw(client, slug)
    except httpx.HTTPError:
        return False
    jobs = raw.get("jobs", [])
    if not jobs:
        return False
    # Greenhouse exposes company_name per job — a real confirmation signal,
    # not just "a board happened to exist at this slug."
    return _company_name_matches(company_name, jobs[0].get("company_name"))


async def _probe_lever(client: httpx.AsyncClient, slug: str) -> bool:
    try:
        raw = await fetch_lever_raw(client, slug)
    except httpx.HTTPError:
        return False
    # Lever exposes no company-name field to cross-check against — a
    # non-empty board at a slug we derived from the target company's own
    # name is accepted as sufficient confirmation. Weaker than the
    # Greenhouse check; a documented, accepted tradeoff.
    return len(raw) > 0


async def _probe_ashby(client: httpx.AsyncClient, slug: str) -> bool:
    try:
        raw = await fetch_ashby_raw(client, slug)
    except httpx.HTTPError:
        return False
    return len(raw.get("jobs", [])) > 0


async def probe_company_board(
    client: httpx.AsyncClient, company_name: str
) -> tuple[str, str] | None:
    """Try each ATS's standard URL pattern for this company.

    Returns (ats_type, board_token) on the first confirmed match, else None.
    Order (greenhouse, lever, ashby) is arbitrary — no signal favors one ATS
    over another at the probing stage.
    """
    for slug in candidate_slugs(company_name):
        if await _probe_greenhouse(client, company_name, slug):
            return ("greenhouse", slug)
        if await _probe_lever(client, slug):
            return ("lever", slug)
        if await _probe_ashby(client, slug):
            return ("ashby", slug)
    return None
