"""SQLite persistence: seen_jobs, company_research, the dealbreaker blocklist
and audit log, and company_expansion_attempts.

Plain sqlite3 (stdlib), synchronous. State operations are small, local, and
infrequent relative to the LLM calls elsewhere in the pipeline — no async
driver needed.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

from agent.models import CompanyResearchOutput, Posting

RETENTION_DAYS = 30
# The whole research result refreshes together (one LLM call per company per
# cache-miss/expiry); a refresh fires once the stability signal — the most
# volatile field — is older than this.
STABILITY_REFRESH_DAYS = 14
# Self-expansion: don't re-probe a company that recently failed to
# turn up a direct ATS board — most probes are negative, and companies
# rarely stand up a new Greenhouse/Lever/Ashby board day to day.
EXPANSION_RETRY_COOLDOWN_DAYS = 30

SCHEMA = """
CREATE TABLE IF NOT EXISTS seen_jobs (
    job_id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    company TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    tier1_result TEXT,
    tier2_result_json TEXT,
    included_in_digest_at TEXT,
    content_fingerprint TEXT
);

CREATE INDEX IF NOT EXISTS idx_seen_jobs_content_fingerprint
    ON seen_jobs (content_fingerprint);

CREATE TABLE IF NOT EXISTS company_research (
    company_domain TEXT PRIMARY KEY,
    company_name TEXT,
    pto_type TEXT,
    pto_evidence TEXT,
    pto_source_url TEXT,
    pto_last_checked TEXT,
    stage_funding TEXT,
    stage_source_url TEXT,
    stability_signal TEXT,
    stability_evidence TEXT,
    stability_source_url TEXT,
    stability_source_date TEXT,
    stability_last_checked TEXT,
    rto_reality TEXT,
    rto_evidence TEXT,
    rto_source_url TEXT,
    dealbreaker_verification TEXT,
    dealbreaker_evidence TEXT,
    dealbreaker_source_url TEXT,
    eng_leadership_churn TEXT,
    oncall_load_signal TEXT,
    last_full_research_at TEXT
);

CREATE TABLE IF NOT EXISTS dealbreaker_audit_log (
    company_domain TEXT,
    reason TEXT,
    evidence TEXT,
    source_url TEXT,
    excluded_at TEXT
);

CREATE TABLE IF NOT EXISTS dealbreaker_blocklist (
    company_name TEXT PRIMARY KEY,
    reason TEXT,
    source_url TEXT,
    added_at TEXT
);

CREATE TABLE IF NOT EXISTS company_expansion_attempts (
    company_name TEXT PRIMARY KEY,
    attempted_at TEXT NOT NULL,
    found_ats_type TEXT,
    found_board_token TEXT
);
"""


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def _parse_iso(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


# --- seen_jobs ---------------------------------------------------------


def purge_expired_seen_jobs(conn: sqlite3.Connection, now: datetime) -> int:
    """Delete seen_jobs rows older than RETENTION_DAYS. Returns rows deleted."""
    cutoff = (now - timedelta(days=RETENTION_DAYS)).isoformat()
    cursor = conn.execute("DELETE FROM seen_jobs WHERE first_seen_at < ?", (cutoff,))
    conn.commit()
    return cursor.rowcount


def is_seen(conn: sqlite3.Connection, job_id: str) -> bool:
    row = conn.execute("SELECT 1 FROM seen_jobs WHERE job_id = ?", (job_id,)).fetchone()
    return row is not None


def is_fingerprint_seen(conn: sqlite3.Connection, content_fingerprint: str) -> bool:
    """Cross-source, cross-run dedup check.

    Catches the same real job re-appearing from a *different* source than
    the one that first recorded it — e.g. seen via a company's direct
    Greenhouse board on one run, then surfaced again via Adzuna's
    aggregation of that same posting on a later run. dedupe_cross_source()
    in agent/pipeline/dedupe.py handles the same-run case; this handles the
    cross-run case.
    """
    row = conn.execute(
        "SELECT 1 FROM seen_jobs WHERE content_fingerprint = ?", (content_fingerprint,)
    ).fetchone()
    return row is not None


def record_seen_job(
    conn: sqlite3.Connection,
    posting: Posting,
    first_seen_at: datetime,
    tier1_result: str | None = None,
    tier2_result_json: str | None = None,
) -> None:
    conn.execute(
        """INSERT INTO seen_jobs
               (job_id, source, company, first_seen_at, tier1_result, tier2_result_json,
                content_fingerprint)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(job_id) DO UPDATE SET
               tier1_result = excluded.tier1_result,
               tier2_result_json = excluded.tier2_result_json""",
        (
            posting.job_id,
            posting.source,
            posting.company,
            first_seen_at.isoformat(),
            tier1_result,
            tier2_result_json,
            posting.content_fingerprint,
        ),
    )
    conn.commit()


def mark_included_in_digest(conn: sqlite3.Connection, job_id: str, when: datetime) -> None:
    conn.execute(
        "UPDATE seen_jobs SET included_in_digest_at = ? WHERE job_id = ?",
        (when.isoformat(), job_id),
    )
    conn.commit()


# --- company_research ---------------------------------------------------


@dataclass
class CompanyResearchRow:
    company_domain: str
    company_name: str | None
    research: CompanyResearchOutput
    pto_last_checked: datetime | None
    stability_last_checked: datetime | None
    last_full_research_at: datetime | None


def get_company_research(
    conn: sqlite3.Connection, company_domain: str
) -> CompanyResearchRow | None:
    cursor = conn.execute(
        "SELECT * FROM company_research WHERE company_domain = ?", (company_domain,)
    )
    row = cursor.fetchone()
    if row is None:
        return None

    columns = [d[0] for d in cursor.description]
    data = dict(zip(columns, row, strict=True))
    research = CompanyResearchOutput(**{k: data[k] for k in CompanyResearchOutput.model_fields})
    return CompanyResearchRow(
        company_domain=data["company_domain"],
        company_name=data["company_name"],
        research=research,
        pto_last_checked=_parse_iso(data["pto_last_checked"]),
        stability_last_checked=_parse_iso(data["stability_last_checked"]),
        last_full_research_at=_parse_iso(data["last_full_research_at"]),
    )


def research_needs_refresh(row: CompanyResearchRow | None, now: datetime) -> bool:
    """True on cache miss or once the stability refresh cadence has expired."""
    if row is None or row.stability_last_checked is None:
        return True
    return now - row.stability_last_checked > timedelta(days=STABILITY_REFRESH_DAYS)


def upsert_company_research(
    conn: sqlite3.Connection,
    company_domain: str,
    company_name: str,
    research: CompanyResearchOutput,
    now: datetime,
) -> None:
    now_iso = now.isoformat()
    conn.execute(
        """INSERT INTO company_research (
               company_domain, company_name,
               pto_type, pto_evidence, pto_source_url, pto_last_checked,
               stage_funding, stage_source_url,
               stability_signal, stability_evidence, stability_source_url,
               stability_source_date, stability_last_checked,
               rto_reality, rto_evidence, rto_source_url,
               dealbreaker_verification, dealbreaker_evidence, dealbreaker_source_url,
               eng_leadership_churn, oncall_load_signal, last_full_research_at
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(company_domain) DO UPDATE SET
               company_name = excluded.company_name,
               pto_type = excluded.pto_type,
               pto_evidence = excluded.pto_evidence,
               pto_source_url = excluded.pto_source_url,
               pto_last_checked = excluded.pto_last_checked,
               stage_funding = excluded.stage_funding,
               stage_source_url = excluded.stage_source_url,
               stability_signal = excluded.stability_signal,
               stability_evidence = excluded.stability_evidence,
               stability_source_url = excluded.stability_source_url,
               stability_source_date = excluded.stability_source_date,
               stability_last_checked = excluded.stability_last_checked,
               rto_reality = excluded.rto_reality,
               rto_evidence = excluded.rto_evidence,
               rto_source_url = excluded.rto_source_url,
               dealbreaker_verification = excluded.dealbreaker_verification,
               dealbreaker_evidence = excluded.dealbreaker_evidence,
               dealbreaker_source_url = excluded.dealbreaker_source_url,
               eng_leadership_churn = excluded.eng_leadership_churn,
               oncall_load_signal = excluded.oncall_load_signal,
               last_full_research_at = excluded.last_full_research_at""",
        (
            company_domain,
            company_name,
            research.pto_type,
            research.pto_evidence,
            research.pto_source_url,
            now_iso,
            research.stage_funding,
            research.stage_source_url,
            research.stability_signal,
            research.stability_evidence,
            research.stability_source_url,
            research.stability_source_date,
            now_iso,
            research.rto_reality,
            research.rto_evidence,
            research.rto_source_url,
            research.dealbreaker_verification,
            research.dealbreaker_evidence,
            research.dealbreaker_source_url,
            research.eng_leadership_churn,
            research.oncall_load_signal,
            now_iso,
        ),
    )
    conn.commit()


# --- dealbreaker blocklist + audit log --------------------------------


def get_dealbreaker_blocklist(conn: sqlite3.Connection) -> set[str]:
    """Lowercased company names — what agent.pipeline.tier1.tier1_filter expects."""
    rows = conn.execute("SELECT company_name FROM dealbreaker_blocklist").fetchall()
    return {r[0] for r in rows}


def record_dealbreaker(
    conn: sqlite3.Connection,
    company_name: str,
    company_domain: str,
    reason: str,
    evidence: str,
    source_url: str | None,
    now: datetime,
) -> None:
    """Add a company to the Tier 1 blocklist and log the evidence.

    Self-reinforcing: once a company is here, tier1_filter rejects it before
    any future LLM call.
    """
    conn.execute(
        """INSERT INTO dealbreaker_blocklist (company_name, reason, source_url, added_at)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(company_name) DO NOTHING""",
        (company_name.strip().lower(), reason, source_url, now.isoformat()),
    )
    conn.execute(
        """INSERT INTO dealbreaker_audit_log
               (company_domain, reason, evidence, source_url, excluded_at)
           VALUES (?, ?, ?, ?, ?)""",
        (company_domain, reason, evidence, source_url, now.isoformat()),
    )
    conn.commit()


# --- company self-expansion --------------------------------------------


def get_discovered_companies(conn: sqlite3.Connection) -> list[dict]:
    """Companies self-expansion has confirmed a direct ATS board for, in the
    same shape as a profile seed entry. Merged with the profile's
    seed_companies at pipeline start (run_pipeline.merge_companies).

    A board that later 404s is re-probed and its found_ats_type reset to NULL,
    so it drops out of this list automatically.
    """
    rows = conn.execute(
        "SELECT company_name, found_ats_type, found_board_token "
        "FROM company_expansion_attempts WHERE found_ats_type IS NOT NULL"
    ).fetchall()
    return [
        {"name": name, "ats_type": ats_type, "board_token": board_token, "domain": None}
        for name, ats_type, board_token in rows
    ]


def should_attempt_expansion(conn: sqlite3.Connection, company_name: str, now: datetime) -> bool:
    """True if this company has never been probed, or its last probe is
    older than EXPANSION_RETRY_COOLDOWN_DAYS.
    """
    row = conn.execute(
        "SELECT attempted_at FROM company_expansion_attempts WHERE company_name = ?",
        (company_name.strip().lower(),),
    ).fetchone()
    if row is None:
        return True
    last_attempted = datetime.fromisoformat(row[0])
    return now - last_attempted > timedelta(days=EXPANSION_RETRY_COOLDOWN_DAYS)


def record_expansion_attempt(
    conn: sqlite3.Connection,
    company_name: str,
    now: datetime,
    found_ats_type: str | None = None,
    found_board_token: str | None = None,
) -> None:
    conn.execute(
        """INSERT INTO company_expansion_attempts
               (company_name, attempted_at, found_ats_type, found_board_token)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(company_name) DO UPDATE SET
               attempted_at = excluded.attempted_at,
               found_ats_type = excluded.found_ats_type,
               found_board_token = excluded.found_board_token""",
        (company_name.strip().lower(), now.isoformat(), found_ats_type, found_board_token),
    )
    conn.commit()
