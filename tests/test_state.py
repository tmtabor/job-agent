"""Unit tests for agent/state.py.

Always an in-memory SQLite DB — never a real one.
"""

import sqlite3
from datetime import datetime, timedelta

import pytest

from agent import state
from agent.models import CompanyResearchOutput, Posting


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")
    state.init_db(connection)
    yield connection
    connection.close()


def _posting(**overrides) -> Posting:
    defaults = {
        "source": "greenhouse",
        "source_native_id": "123",
        "company": "Example Corp",
        "title": "Staff Software Engineer",
        "location": "Remote",
        "remote": True,
        "department": "Engineering",
        "description_text": "Build things.",
        "compensation_text": None,
        "apply_url": "https://example.com/jobs/123",
    }
    defaults.update(overrides)
    return Posting(**defaults)


def _research(**overrides) -> CompanyResearchOutput:
    defaults = {
        "pto_type": "unspecified",
        "pto_evidence": "No evidence found.",
        "pto_source_url": None,
        "stage_funding": None,
        "stage_source_url": None,
        "stability_signal": "unclear",
        "stability_evidence": "No evidence found.",
        "stability_source_url": None,
        "stability_source_date": None,
        "rto_reality": "unclear",
        "rto_evidence": "No evidence found.",
        "rto_source_url": None,
        "dealbreaker_verification": "none_found",
        "dealbreaker_evidence": None,
        "dealbreaker_source_url": None,
        "eng_leadership_churn": None,
        "oncall_load_signal": None,
    }
    defaults.update(overrides)
    return CompanyResearchOutput(**defaults)


# --- seen_jobs ---


def test_is_seen_false_for_unknown_job(conn):
    assert state.is_seen(conn, "nonexistent") is False


def test_record_and_check_seen_job(conn):
    posting = _posting()
    state.record_seen_job(conn, posting, first_seen_at=datetime(2026, 7, 1), tier1_result="pass")
    assert state.is_seen(conn, posting.job_id) is True


def test_is_fingerprint_seen_false_for_unknown_fingerprint(conn):
    assert state.is_fingerprint_seen(conn, "nonexistent") is False


def test_is_fingerprint_seen_true_after_recording(conn):
    posting = _posting()
    state.record_seen_job(conn, posting, first_seen_at=datetime(2026, 7, 1))
    assert state.is_fingerprint_seen(conn, posting.content_fingerprint) is True


def test_is_fingerprint_seen_catches_same_job_from_different_source(conn):
    """Cross-source, cross-run case: recorded via Greenhouse on one run,
    later fetched again via Adzuna's aggregation of the same posting.
    """
    greenhouse_posting = _posting(source="greenhouse", source_native_id="123")
    state.record_seen_job(conn, greenhouse_posting, first_seen_at=datetime(2026, 7, 1))

    adzuna_posting = _posting(source="adzuna", source_native_id="999")
    assert state.is_seen(conn, adzuna_posting.job_id) is False  # different job_id
    assert state.is_fingerprint_seen(conn, adzuna_posting.content_fingerprint) is True


def test_record_seen_job_upserts_on_conflict(conn):
    posting = _posting()
    state.record_seen_job(conn, posting, first_seen_at=datetime(2026, 7, 1), tier1_result="pass")
    state.record_seen_job(
        conn,
        posting,
        first_seen_at=datetime(2026, 7, 1),
        tier1_result="pass",
        tier2_result_json='{"overall_fit_score": 8}',
    )
    row = conn.execute(
        "SELECT tier2_result_json FROM seen_jobs WHERE job_id = ?", (posting.job_id,)
    ).fetchone()
    assert row[0] == '{"overall_fit_score": 8}'


def test_purge_expired_seen_jobs_removes_old_rows_only(conn):
    old_posting = _posting(source_native_id="old")
    new_posting = _posting(source_native_id="new")
    now = datetime(2026, 7, 24)

    state.record_seen_job(conn, old_posting, first_seen_at=now - timedelta(days=40))
    state.record_seen_job(conn, new_posting, first_seen_at=now - timedelta(days=5))

    deleted = state.purge_expired_seen_jobs(conn, now)

    assert deleted == 1
    assert state.is_seen(conn, old_posting.job_id) is False
    assert state.is_seen(conn, new_posting.job_id) is True


def test_mark_included_in_digest(conn):
    posting = _posting()
    state.record_seen_job(conn, posting, first_seen_at=datetime(2026, 7, 1))
    state.mark_included_in_digest(conn, posting.job_id, datetime(2026, 7, 24))
    row = conn.execute(
        "SELECT included_in_digest_at FROM seen_jobs WHERE job_id = ?", (posting.job_id,)
    ).fetchone()
    assert row[0] == datetime(2026, 7, 24).isoformat()


# --- company_research ---


def test_get_company_research_returns_none_on_cache_miss(conn):
    assert state.get_company_research(conn, "example.com") is None


def test_research_needs_refresh_true_on_cache_miss(conn):
    assert state.research_needs_refresh(None, datetime(2026, 7, 24)) is True


def test_upsert_and_get_company_research_roundtrip(conn):
    research = _research(stability_signal="stable", stability_evidence="Series C, hiring.")
    now = datetime(2026, 7, 24)
    state.upsert_company_research(conn, "example.com", "Example Corp", research, now)

    row = state.get_company_research(conn, "example.com")
    assert row is not None
    assert row.company_name == "Example Corp"
    assert row.research.stability_signal == "stable"
    assert row.stability_last_checked == now


def test_research_needs_refresh_false_when_recently_checked(conn):
    research = _research()
    now = datetime(2026, 7, 24)
    state.upsert_company_research(conn, "example.com", "Example Corp", research, now)
    row = state.get_company_research(conn, "example.com")

    assert state.research_needs_refresh(row, now + timedelta(days=5)) is False


def test_research_needs_refresh_true_after_stability_cadence_expires(conn):
    research = _research()
    now = datetime(2026, 7, 24)
    state.upsert_company_research(conn, "example.com", "Example Corp", research, now)
    row = state.get_company_research(conn, "example.com")

    assert state.research_needs_refresh(row, now + timedelta(days=15)) is True


# --- dealbreaker blocklist + audit log ---


def test_get_dealbreaker_blocklist_empty_initially(conn):
    assert state.get_dealbreaker_blocklist(conn) == set()


def test_record_dealbreaker_adds_to_blocklist_and_audit_log(conn):
    now = datetime(2026, 7, 24)
    state.record_dealbreaker(
        conn,
        company_name="Shady Crypto Co",
        company_domain="shadycrypto.com",
        reason="crypto exchange",
        evidence="Operates a cryptocurrency exchange per their own site.",
        source_url="https://shadycrypto.com/about",
        now=now,
    )

    assert state.get_dealbreaker_blocklist(conn) == {"shady crypto co"}

    audit_row = conn.execute("SELECT company_domain, reason FROM dealbreaker_audit_log").fetchone()
    assert audit_row == ("shadycrypto.com", "crypto exchange")


def test_record_dealbreaker_is_idempotent_on_blocklist(conn):
    now = datetime(2026, 7, 24)
    state.record_dealbreaker(conn, "Shady Co", "shady.com", "reason1", "evidence1", None, now)
    state.record_dealbreaker(conn, "Shady Co", "shady.com", "reason2", "evidence2", None, now)

    assert state.get_dealbreaker_blocklist(conn) == {"shady co"}
    audit_count = conn.execute("SELECT COUNT(*) FROM dealbreaker_audit_log").fetchone()[0]
    assert audit_count == 2  # audit log is append-only, unlike the blocklist


# --- company self-expansion ---


def test_should_attempt_expansion_true_for_never_attempted_company(conn):
    assert state.should_attempt_expansion(conn, "New Co", datetime(2026, 7, 24)) is True


def test_should_attempt_expansion_false_within_cooldown(conn):
    now = datetime(2026, 7, 24)
    state.record_expansion_attempt(conn, "New Co", now)
    assert state.should_attempt_expansion(conn, "New Co", now + timedelta(days=5)) is False


def test_should_attempt_expansion_true_after_cooldown_expires(conn):
    now = datetime(2026, 7, 24)
    state.record_expansion_attempt(conn, "New Co", now)
    assert state.should_attempt_expansion(conn, "New Co", now + timedelta(days=31)) is True


def test_should_attempt_expansion_is_case_insensitive(conn):
    now = datetime(2026, 7, 24)
    state.record_expansion_attempt(conn, "New Co", now)
    assert state.should_attempt_expansion(conn, "NEW CO", now + timedelta(days=5)) is False


def test_record_expansion_attempt_stores_found_board(conn):
    now = datetime(2026, 7, 24)
    state.record_expansion_attempt(
        conn, "New Co", now, found_ats_type="greenhouse", found_board_token="newco"
    )
    row = conn.execute(
        "SELECT found_ats_type, found_board_token FROM company_expansion_attempts "
        "WHERE company_name = 'new co'"
    ).fetchone()
    assert row == ("greenhouse", "newco")


def test_record_expansion_attempt_upserts_on_conflict(conn):
    now = datetime(2026, 7, 24)
    state.record_expansion_attempt(conn, "New Co", now)  # first attempt: not found
    later = now + timedelta(days=31)
    state.record_expansion_attempt(
        conn, "New Co", later, found_ats_type="lever", found_board_token="newco"
    )  # second attempt: found

    row = conn.execute(
        "SELECT attempted_at, found_ats_type FROM company_expansion_attempts "
        "WHERE company_name = 'new co'"
    ).fetchone()
    assert row == (later.isoformat(), "lever")


def test_get_discovered_companies_returns_only_confirmed_boards(conn):
    now = datetime(2026, 7, 24)
    state.record_expansion_attempt(conn, "Unconfirmed Co", now)  # probed, no board
    state.record_expansion_attempt(
        conn, "Confirmed Co", now, found_ats_type="lever", found_board_token="confirmedco"
    )

    discovered = state.get_discovered_companies(conn)

    assert discovered == [
        {
            "name": "confirmed co",
            "ats_type": "lever",
            "board_token": "confirmedco",
            "domain": None,
        }
    ]


def test_get_discovered_companies_drops_board_that_later_404s(conn):
    now = datetime(2026, 7, 24)
    state.record_expansion_attempt(
        conn, "Flaky Co", now, found_ats_type="ashby", found_board_token="flakyco"
    )
    # Re-probed later, board gone -> found_ats_type reset to NULL.
    state.record_expansion_attempt(conn, "Flaky Co", now + timedelta(days=31))

    assert state.get_discovered_companies(conn) == []
