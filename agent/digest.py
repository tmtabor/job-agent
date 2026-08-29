"""Digest email HTML builder.

A simple ranked list grouped by bucket. All external-sourced text (posting
titles, company names, summaries) is HTML-escaped before embedding, since it
originates from third-party job postings, not from us.

Compact entry layout + company breakdown + inline styling: salary and PTO
days are the two things meant to be skimmable at a glance, so they're pulled
onto one
highlighted stats line per entry instead of scattered across several plain
paragraphs. A <style> block (not per-element inline styles) is used for
brevity — acceptable here since this is a personal digest read in a modern
mail client (Gmail/Apple Mail), not a mass-marketing send that needs to
survive every legacy email client's CSS stripping.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from html import escape

from agent.models import JobEvaluation
from agent.pipeline.post_process import BucketedDigest, DigestEntry

_PTO_TYPE_LABELS = {
    "generous_accrued": "Generous accrued PTO",
    "standard_accrued": "Standard accrued PTO",
    "flex_unlimited": "Unlimited/flexible PTO",
    "unspecified": "PTO not specified",
}

_STYLE = """
    body { font-family: -apple-system, Helvetica, Arial, sans-serif; color: #1a1a1a;
           max-width: 640px; margin: 0 auto; padding: 16px; line-height: 1.4; }
    h1 { font-size: 20px; margin: 0 0 12px; }
    h2 { font-size: 15px; margin: 20px 0 8px; border-bottom: 2px solid #ddd; padding-bottom: 4px; }
    .breakdown-list { list-style: none; margin: 0; padding: 0; }
    .breakdown-list li { display: inline-block; background: #f0f0f0; border-radius: 4px;
                          padding: 3px 9px; margin: 0 6px 6px 0; font-size: 13px; }
    .scope-list { list-style: none; margin: 0; padding: 0; font-size: 13px; }
    .scope-list li { padding: 2px 0; }
    .entry { border-bottom: 1px solid #e5e5e5; padding: 10px 0; }
    .entry h3 { font-size: 15px; margin: 0 0 4px; }
    .entry h3 a { color: #0645ad; text-decoration: none; }
    .meta { color: #666; font-size: 12px; margin: 0 0 4px; }
    .stats { font-size: 13px; margin: 0 0 4px; }
    .stats strong { background: #eef6ec; color: #1a5c1a; padding: 1px 6px; border-radius: 3px; }
    .summary { font-size: 13px; margin: 0; }
    .conflict { font-size: 12px; color: #8a5a00; margin: 4px 0 0; }
    footer { color: #888; font-size: 12px; margin-top: 20px; }
"""


def _pto_display(evaluation: JobEvaluation) -> str:
    """A specific day count when we have one, else the PTO category —
    never blank, since PTO is one of the two things this digest is meant to
    make skimmable (the other being comp).
    """
    if evaluation.pto_days_estimate is not None:
        return f"{evaluation.pto_days_estimate} days PTO"
    return _PTO_TYPE_LABELS.get(evaluation.pto_type, evaluation.pto_type)


@dataclass
class RunStats:
    """Run-scope counters for the digest's top-of-email summary section.

    Computed by scripts/run_pipeline.py, which is the only place with
    visibility into every pipeline stage (fetch, tier1, company research,
    tier2) — digest.py just renders whatever it's handed.
    """

    companies_researched: int
    new_roles_found: int
    eliminated_by_prefilter: int
    eliminated_by_llm: int
    remaining: int
    estimated_cost_usd: Decimal


def _scope_html(stats: RunStats) -> str:
    rows = [
        ("Companies researched", str(stats.companies_researched)),
        ("New roles found", str(stats.new_roles_found)),
        ("Eliminated by pre-filtering", str(stats.eliminated_by_prefilter)),
        ("Eliminated by LLM evaluation", str(stats.eliminated_by_llm)),
        ("Roles remaining", str(stats.remaining)),
        ("Estimated LLM cost", f"${stats.estimated_cost_usd:.4f}"),
    ]
    items = "\n".join(
        f"      <li><strong>{escape(label)}:</strong> {escape(value)}</li>" for label, value in rows
    )
    return f'  <section>\n    <h2>Run scope</h2>\n    <ul class="scope-list">\n{items}\n    </ul>\n  </section>'


def _company_breakdown(bucketed: BucketedDigest) -> list[tuple[str, int]]:
    """Company -> count of matching roles across every bucket, alphabetical
    by company name — the "Anthropic (13)" style summary at the top of the
    email.
    """
    counts: dict[str, int] = {}
    for entry in bucketed.main + bucketed.unstated_comp + bucketed.ambiguous_level:
        counts[entry.posting.company] = counts.get(entry.posting.company, 0) + 1
    return sorted(counts.items(), key=lambda item: item[0].lower())


def total_match_count(bucketed: BucketedDigest) -> int:
    """Total matching roles across every bucket — used for both the
    company-breakdown counts above and the email subject line
    (scripts/run_pipeline.py), so both always agree on what "found" means.
    """
    return len(bucketed.main) + len(bucketed.unstated_comp) + len(bucketed.ambiguous_level)


def _breakdown_html(bucketed: BucketedDigest) -> str:
    breakdown = _company_breakdown(bucketed)
    if not breakdown:
        return ""
    items = "\n".join(f"      <li>{escape(company)} ({count})</li>" for company, count in breakdown)
    return (
        f'  <section>\n    <h2>Matches by company</h2>\n    <ul class="breakdown-list">\n'
        f"{items}\n    </ul>\n  </section>"
    )


def _entry_html(entry: DigestEntry) -> str:
    posting = entry.posting
    evaluation = entry.evaluation

    comp_display = evaluation.stated_comp or "not listed"

    level_line = ""
    if evaluation.level_match == "ambiguous":
        level_line = (
            f"<p><strong>Level:</strong> ambiguous — {escape(evaluation.level_reasoning)}</p>\n"
        )

    conflict_line = ""
    if evaluation.company_research_conflict:
        conflict_line = (
            f'<p class="conflict"><strong>Research conflict:</strong> '
            f"{escape(evaluation.company_research_conflict)}</p>\n"
        )

    return f"""    <div class="entry">
      <h3><a href="{escape(posting.apply_url)}">{escape(posting.title)}</a></h3>
      <p class="meta">{escape(posting.company)} — {escape(posting.location or "location not listed")}
         &middot; Fit {evaluation.overall_fit_score}/10</p>
      <p class="stats"><strong>{escape(comp_display)}</strong>
         &middot; <strong>{escape(_pto_display(evaluation))}</strong>
         &middot; WLB: {escape(evaluation.wlb_signal)}</p>
      <p class="summary">{escape(evaluation.summary)}</p>
{level_line}{conflict_line}    </div>"""


def _section_html(title: str, entries: list[DigestEntry]) -> str:
    if not entries:
        return ""
    items = "\n".join(_entry_html(entry) for entry in entries)
    return f"  <section>\n    <h2>{escape(title)}</h2>\n{items}\n  </section>"


def build_digest_html(
    bucketed: BucketedDigest,
    skipped_summary: str | None = None,
    stats: RunStats | None = None,
) -> str:
    """Render the full digest email HTML.

    Args:
        bucketed: Output of agent.pipeline.post_process.bucket_entries().
        skipped_summary: Optional "N sources/postings skipped this run" line,
            rendered in the email footer.
        stats: Optional run-scope counters (added 2026-07-26 per user
            request), rendered as its own section above everything else —
            it answers "what happened this run" before "what did it find."
            Optional (defaults to omitted) so existing callers/tests that
            don't have a RunStats handy still render a valid digest.
    """
    sections = [
        _scope_html(stats) if stats is not None else "",
        _breakdown_html(bucketed),
        _section_html("Top matches", bucketed.main),
        _section_html("Unstated compensation", bucketed.unstated_comp),
        _section_html("Ambiguous level", bucketed.ambiguous_level),
    ]
    body = "\n".join(section for section in sections if section)

    if not body:
        body = "  <p>No postings passed scoring today.</p>"

    footer = (
        f"  <footer>\n    <p>{escape(skipped_summary)}</p>\n  </footer>" if skipped_summary else ""
    )

    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<style>{_STYLE}</style>
</head>
<body>
  <h1>Job Digest</h1>
{body}
{footer}
</body>
</html>"""
