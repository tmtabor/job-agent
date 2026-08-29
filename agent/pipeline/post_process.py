"""Post-processing: hard-reject rules and digest bucketing."""

from __future__ import annotations

from dataclasses import dataclass

from agent.models import JobEvaluation, Posting


@dataclass
class DigestEntry:
    posting: Posting
    evaluation: JobEvaluation


@dataclass
class BucketedDigest:
    main: list[DigestEntry]
    unstated_comp: list[DigestEntry]
    ambiguous_level: list[DigestEntry]


def is_hard_rejected(evaluation: JobEvaluation) -> bool:
    """Never appears in the digest if any of these hold."""
    return (
        evaluation.dealbreaker
        or evaluation.comp_meets_bar == "no"
        or not evaluation.location_match
        or evaluation.level_match == "no"
    )


def bucket_entries(entries: list[DigestEntry]) -> BucketedDigest:
    """Hard-reject, then assign each survivor to exactly one bucket.

    Precedence when a survivor qualifies for more than one special bucket
    (e.g. unstated comp AND ambiguous level): unstated comp wins — an unknown
    number is a more fundamental gap than an inferred level judgment.

    Research conflicts are not a bucket of their own — they're an
    inline flag on whichever bucket the entry lands in, read directly off
    evaluation.company_research_conflict by the digest builder.
    """
    main: list[DigestEntry] = []
    unstated_comp: list[DigestEntry] = []
    ambiguous_level: list[DigestEntry] = []

    for entry in entries:
        evaluation = entry.evaluation
        if is_hard_rejected(evaluation):
            continue
        if evaluation.comp_meets_bar == "unstated":
            unstated_comp.append(entry)
        elif evaluation.level_match == "ambiguous":
            ambiguous_level.append(entry)
        else:
            main.append(entry)

    def by_fit_score_desc(entry: DigestEntry) -> int:
        return -entry.evaluation.overall_fit_score

    return BucketedDigest(
        main=sorted(main, key=by_fit_score_desc),
        unstated_comp=sorted(unstated_comp, key=by_fit_score_desc),
        ambiguous_level=sorted(ambiguous_level, key=by_fit_score_desc),
    )
