#!/usr/bin/env python3
"""Daily job-scan pipeline orchestrator.

    fetch_sources -> dedupe -> tier1_filter -> company_research ->
    tier2_score -> post_process -> digest_build -> deliver -> persist_state

A failure in one source or one posting's scoring must not abort the run —
every external call goes through agent.pipeline.retry.call_with_retry and a
failure there is logged and skipped, never raised.
"""

from __future__ import annotations

import asyncio
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

# Running this file directly (`python scripts/run_pipeline.py` — the
# documented command, and what the GitHub Actions workflow uses) only adds
# scripts/ itself to sys.path, not the repo root, so `agent` isn't
# importable without this. Confirmed live 2026-07-25: the exact documented
# invocation failed with `ModuleNotFoundError: No module named 'agent'`
# before this fix — `python -m scripts.run_pipeline` or anything going
# through pytest worked around it by accident (both put the repo root on
# sys.path some other way), which is why this went unnoticed until an
# actual real run was attempted standalone.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
from pydantic_ai.usage import RunUsage

from agent import state
from agent.agents import build_research_input, build_scoring_input, research_company, score_posting
from agent.agents.company_research import SearchResult
from agent.config import settings
from agent.cost import estimate_cost_usd
from agent.delivery.postmark import send_digest
from agent.digest import RunStats, build_digest_html, total_match_count
from agent.logging import configure_logging, get_logger
from agent.models import CompanyResearchOutput, Posting
from agent.pipeline.dedupe import dedupe_cross_source
from agent.pipeline.post_process import DigestEntry, bucket_entries
from agent.pipeline.retry import SkippedUnit, call_with_retry
from agent.pipeline.self_expansion import probe_company_board
from agent.pipeline.tier1 import compile_tier1_patterns, tier1_filter
from agent.profile import Profile, SourceQueries, load_profile
from agent.prompts.templates import render_system_prompt
from agent.sources.adzuna import fetch_adzuna_all_pages, normalize_adzuna
from agent.sources.ashby import fetch_ashby_raw, normalize_ashby
from agent.sources.greenhouse import fetch_greenhouse_raw, normalize_greenhouse
from agent.sources.jobspy_source import fetch_jobspy_raw, normalize_jobspy
from agent.sources.lever import fetch_lever_raw, normalize_lever
from agent.sources.tavily import tavily_search

logger = get_logger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = REPO_ROOT / "data" / "seen_jobs.db"

# Adzuna pages per run, not total results — the free tier is ~1,000
# calls/month (~33/day) shared across every source, so this stays small.
ADZUNA_MAX_PAGES = 3

# JobSpy (LinkedIn/Indeed/Glassdoor/ZipRecruiter) — a scraper, not an API,
# and the flakiest source. jobspy.scrape_jobs() already isolates per-site
# failures internally, so no per-site handling is needed here beyond the same
# retry/skip wrapper every other source goes through (verified against the
# live scraper, 2026-07-24). Modest defaults to keep run time and scraping
# volume bounded.
JOBSPY_SITES = ["indeed", "linkedin", "glassdoor", "zip_recruiter"]
JOBSPY_RESULTS_WANTED = 20
JOBSPY_HOURS_OLD = 72

# Cap probe attempts per run — Adzuna/JobSpy can easily surface dozens of
# untracked company names in one run, and most probes are negative (no public
# board), so this bounds run time and probe volume rather than trying every
# untracked company every single day.
MAX_EXPANSION_ATTEMPTS_PER_RUN = 10

# Company research query. NOT "{company} company" — verified live 2026-07-24
# that this only surfaces generic company-overview pages (Wikipedia, "what is
# X" explainers), never PTO / RTO reality / stability content.
#
# Deliberately does NOT include "Glassdoor" as a keyword: Glassdoor ranks near
# the top for review-style queries, but its pages return zero raw_content (see
# agent/sources/tavily.py) — it blocks content extraction entirely, gating
# review text behind a login wall, so biasing the query toward it wastes
# result slots on pages we can't read. "levels.fyi blind" instead surfaces
# sources that don't block scraping.
COMPANY_RESEARCH_QUERY_TEMPLATE = (
    "{company} benefits PTO levels.fyi blind layoffs return to office reviews"
)
COMPANY_RESEARCH_MAX_RESULTS = 8


#: Sources whose postings are "discovered," not directly tracked in the
#: profile's seed_companies — candidates for self-expansion. Direct ATS boards
#: (greenhouse/lever/ashby) are already tracked by definition and excluded.
DISCOVERY_SOURCES = {"adzuna", "jobspy"}


def merge_companies(seed: list[dict], discovered: list[dict]) -> list[dict]:
    """Combine the profile's seed list with self-expansion discoveries from
    the state DB. A seed entry wins on a name collision — it carries the real
    canonical domain, which a discovery never has."""
    by_name = {c["name"].strip().lower(): c for c in seed}
    for company in discovered:
        by_name.setdefault(company["name"].strip().lower(), company)
    return list(by_name.values())


async def expand_companies_from_discovery_sources(
    client: httpx.AsyncClient,
    conn: sqlite3.Connection,
    postings: list[Posting],
    companies: list[dict],
    now: datetime,
) -> None:
    """Probe for direct ATS boards for companies discovered via Adzuna or
    JobSpy that aren't already tracked. A confirmed board is recorded in the
    state DB (company_expansion_attempts) and merged into the effective
    company list on the next run via merge_companies() — not written back to
    the profile. Both discovery sources feed the same logic.

    Takes effect on future runs, not this one — avoids restructuring the fetch
    flow into two passes for a same-day benefit the discovery source already
    provided this run anyway.
    """
    tracked_names = {c["name"].strip().lower() for c in companies}
    discovered_companies = sorted(
        {
            p.company
            for p in postings
            if p.source in DISCOVERY_SOURCES and p.company.strip().lower() not in tracked_names
        }
    )

    attempts = 0
    for company_name in discovered_companies:
        if attempts >= MAX_EXPANSION_ATTEMPTS_PER_RUN:
            break
        if not state.should_attempt_expansion(conn, company_name, now):
            continue
        attempts += 1

        result = await probe_company_board(client, company_name)
        if result is None:
            state.record_expansion_attempt(conn, company_name, now)
            continue

        ats_type, board_token = result
        state.record_expansion_attempt(conn, company_name, now, ats_type, board_token)
        logger.info(
            "Self-expansion found a direct ATS board",
            extra={"company": company_name, "ats_type": ats_type, "board_token": board_token},
        )


def company_domain_for(company_name: str, companies: list[dict]) -> str:
    """Best-effort canonical domain lookup from the effective company list.

    Falls back to the lowercased display name when no domain is configured
    (e.g. a company discovered only via Adzuna or JobSpy). A known
    simplification of the "canonical domain, not name string" cache-key
    design — real domain resolution for discovered companies is not done.
    """
    for company in companies:
        if company["name"].lower() == company_name.lower() and company.get("domain"):
            return company["domain"]
    return company_name.strip().lower()


async def fetch_all_postings(
    client: httpx.AsyncClient, companies: list[dict], queries: SourceQueries
) -> tuple[list[Posting], list[str]]:
    """Fetch from every configured ATS board, plus Adzuna and JobSpy
    (both discovery sources — see expand_companies_from_discovery_sources).
    """
    postings: list[Posting] = []
    skipped: list[str] = []

    for company in companies:
        name = company["name"]
        ats_type = company["ats_type"]
        token = company["board_token"]
        label = f"{ats_type}:{name}"

        async def do_fetch(ats_type: str = ats_type, token: str = token, name: str = name):
            if ats_type == "greenhouse":
                raw = await fetch_greenhouse_raw(client, token)
                return normalize_greenhouse(raw, company=name)
            if ats_type == "lever":
                raw = await fetch_lever_raw(client, token)
                return normalize_lever(raw, company=name)
            if ats_type == "ashby":
                raw = await fetch_ashby_raw(client, token)
                return normalize_ashby(raw, company=name)
            raise ValueError(f"Unknown ats_type for {name}: {ats_type}")

        result = await call_with_retry(label, do_fetch)
        if isinstance(result, SkippedUnit):
            skipped.append(result.label)
        else:
            postings.extend(result)

    if settings.adzuna_app_id and settings.adzuna_api_key:

        async def do_adzuna():
            results = await fetch_adzuna_all_pages(
                client,
                settings.adzuna_app_id,
                settings.adzuna_api_key,
                what=queries.adzuna_query,
                category=queries.adzuna_category,
                max_pages=ADZUNA_MAX_PAGES,
            )
            return normalize_adzuna(results)

        result = await call_with_retry("adzuna", do_adzuna)
        if isinstance(result, SkippedUnit):
            skipped.append(result.label)
        else:
            postings.extend(result)
    else:
        logger.warning("Adzuna credentials not set — skipping Adzuna source")

    async def do_jobspy():
        rows = await fetch_jobspy_raw(
            JOBSPY_SITES,
            search_term=queries.jobspy_search_term,
            location=queries.jobspy_location,
            results_wanted=JOBSPY_RESULTS_WANTED,
            hours_old=JOBSPY_HOURS_OLD,
        )
        return normalize_jobspy(rows)

    result = await call_with_retry("jobspy", do_jobspy)
    if isinstance(result, SkippedUnit):
        skipped.append(result.label)
    else:
        postings.extend(result)

    return postings, skipped


async def get_or_refresh_company_research(
    client: httpx.AsyncClient,
    conn: sqlite3.Connection,
    company_name: str,
    company_domain: str,
    now: datetime,
    usage: RunUsage | None = None,
) -> CompanyResearchOutput | None:
    row = state.get_company_research(conn, company_domain)
    if not state.research_needs_refresh(row, now):
        return row.research if row else None

    if not settings.tavily_api_key:
        logger.warning(
            "Tavily API key not set — using stale/no research", extra={"company": company_name}
        )
        return row.research if row else None

    async def do_research():
        raw_results = await tavily_search(
            client,
            settings.tavily_api_key,
            query=COMPANY_RESEARCH_QUERY_TEMPLATE.format(company=company_name),
            max_results=COMPANY_RESEARCH_MAX_RESULTS,
            include_raw_content="text",
        )
        search_results = [
            SearchResult(
                r.get("title", ""), r.get("url", ""), r.get("content", ""), r.get("raw_content")
            )
            for r in raw_results
        ]
        research_input = build_research_input(company_name, search_results)
        return await research_company(research_input, usage=usage)

    result = await call_with_retry(f"company_research:{company_name}", do_research)
    if isinstance(result, SkippedUnit):
        return row.research if row else None

    state.upsert_company_research(conn, company_domain, company_name, result, now)

    if result.dealbreaker_verification in ("clear", "adjacent_signal_found"):
        state.record_dealbreaker(
            conn,
            company_name,
            company_domain,
            reason=f"company research: {result.dealbreaker_verification}",
            evidence=result.dealbreaker_evidence or "",
            source_url=result.dealbreaker_source_url,
            now=now,
        )

    return result


async def run_pipeline(
    client: httpx.AsyncClient | None = None,
    db_path: Path | None = None,
    profile_path: Path | None = None,
) -> None:
    """Run one full pipeline pass.

    `client`, `db_path`, and `profile_path` are injectable so integration
    tests can mock every external endpoint via a single httpx.MockTransport
    and use tmp files, without monkeypatching internals. None of the three are
    exposed by the CLI entrypoint below — all default to the real
    client/DB/profile.
    """
    configure_logging()
    now = datetime.now(UTC)

    # Load and validate the profile before any network call, so a typo or
    # missing field fails the run immediately rather than mid-scan.
    profile: Profile = load_profile(profile_path)
    tier1_patterns = compile_tier1_patterns(profile)
    system_prompt = render_system_prompt(profile)

    db_path = db_path or DB_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    state.init_db(conn)
    state.purge_expired_seen_jobs(conn, now)

    seed = [c.model_dump() for c in profile.seed_companies]
    companies = merge_companies(seed, state.get_discovered_companies(conn))
    dealbreaker_companies = state.get_dealbreaker_blocklist(conn)
    skipped_labels: list[str] = []

    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=30.0)
    try:
        postings, fetch_skips = await fetch_all_postings(client, companies, profile.source_queries)
        skipped_labels.extend(fetch_skips)

        # Best-effort: probe for direct ATS boards for companies Adzuna or
        # JobSpy surfaced that aren't tracked yet. Confirmed boards are
        # recorded in the state DB for future runs — never raises, a
        # failed/negative probe is a normal outcome, not a skipped-work event.
        await expand_companies_from_discovery_sources(client, conn, postings, companies, now)

        # Same-run cross-source collapse (e.g. a company's own Greenhouse
        # board and Adzuna's aggregation of that same posting) before the
        # cross-run checks below.
        deduped_postings = dedupe_cross_source(postings)

        new_postings = [
            p
            for p in deduped_postings
            if not state.is_seen(conn, p.job_id)
            and not state.is_fingerprint_seen(conn, p.content_fingerprint)
        ]
        logger.info(
            "Fetched postings",
            extra={
                "total": len(postings),
                "after_cross_source_dedupe": len(deduped_postings),
                "new": len(new_postings),
            },
        )

        entries: list[DigestEntry] = []
        research_usage = RunUsage()
        scoring_usage = RunUsage()
        researched_companies: set[str] = set()
        tier1_reject_count = 0

        for posting in new_postings:
            tier1_result = tier1_filter(posting, dealbreaker_companies, tier1_patterns)
            state.record_seen_job(conn, posting, first_seen_at=now, tier1_result=tier1_result)

            if tier1_result == "reject":
                tier1_reject_count += 1
                continue

            company_domain = company_domain_for(posting.company, companies)
            researched_companies.add(posting.company)
            research = await get_or_refresh_company_research(
                client, conn, posting.company, company_domain, now, usage=research_usage
            )
            research_text = research.model_dump_json(indent=2) if research else None

            scoring_input = build_scoring_input(
                posting_title=posting.title,
                posting_company=posting.company,
                posting_location=posting.location,
                posting_remote=posting.remote,
                posting_description=posting.description_text,
                posting_compensation=posting.compensation_text,
                company_research_text=research_text,
                tier1_result=tier1_result,
            )

            async def do_score(scoring_input: str = scoring_input):
                return await score_posting(
                    scoring_input, system_prompt=system_prompt, usage=scoring_usage
                )

            result = await call_with_retry(f"score:{posting.company}:{posting.title}", do_score)
            if isinstance(result, SkippedUnit):
                skipped_labels.append(result.label)
                continue

            evaluation = result
            state.record_seen_job(
                conn,
                posting,
                first_seen_at=now,
                tier1_result=tier1_result,
                tier2_result_json=evaluation.model_dump_json(),
            )

            # A single posting's dealbreaker=true excludes only that posting
            # (via is_hard_rejected in post_process.py) — it does NOT
            # blocklist the whole company. Confirmed against a real run
            # 2026-07-24: at a large AI lab, one fellowship posting cited a
            # blockchain-security research example and got correctly flagged
            # as crypto-adjacent, which — before this fix — blocklisted all of
            # that company's other 400+ unrelated postings (56 of which scored
            # >=8/10) on every subsequent run. Company-wide blocklisting is now
            # driven only by company_research's dealbreaker_verification (see
            # get_or_refresh_company_research), which assesses the company's
            # overall business, not one role.

            entries.append(DigestEntry(posting=posting, evaluation=evaluation))

        bucketed = bucket_entries(entries)
        for bucket in (bucketed.main, bucketed.unstated_comp, bucketed.ambiguous_level):
            for entry in bucket:
                state.mark_included_in_digest(conn, entry.posting.job_id, now)

        skipped_summary = (
            f"{len(skipped_labels)} sources/postings skipped this run" if skipped_labels else None
        )
        stats = RunStats(
            companies_researched=len(researched_companies),
            new_roles_found=len(new_postings),
            eliminated_by_prefilter=tier1_reject_count,
            # len(entries) is every posting that reached tier2 scoring;
            # bucket_entries() drops the ones is_hard_rejected() catches
            # (dealbreaker, comp/level/location hard-reject) — the gap
            # between the two is exactly what the LLM eliminated.
            eliminated_by_llm=len(entries) - total_match_count(bucketed),
            remaining=total_match_count(bucketed),
            estimated_cost_usd=estimate_cost_usd(research_usage, scoring_usage),
        )
        html = build_digest_html(bucketed, skipped_summary=skipped_summary, stats=stats)

        if settings.postmark_server_token and settings.email_from and settings.email_to:

            async def do_send():
                return await send_digest(
                    client,
                    settings.postmark_server_token,
                    settings.email_from,
                    settings.email_to,
                    subject=f"Job Digest {now:%Y-%m-%d} - {total_match_count(bucketed)} found",
                    html_body=html,
                )

            result = await call_with_retry("postmark_send", do_send)
            if isinstance(result, SkippedUnit):
                logger.error("Failed to send digest email", extra={"error": result.error})
        else:
            logger.warning("Postmark not fully configured — skipping delivery")
    finally:
        if owns_client:
            await client.aclose()

    if skipped_labels:
        logger.warning(
            "Run completed with skipped units",
            extra={"count": len(skipped_labels), "labels": skipped_labels},
        )

    conn.close()


if __name__ == "__main__":
    asyncio.run(run_pipeline())
