from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from scripts.check_label_agreement import (
    CATEGORY_LABELS,
    PRIORITY_LABELS,
    RESPONSE_LABELS,
    SPAM_LABELS,
    _first_label,
    _norm,
)
from scripts.sample_test_v2 import read_csv


TASK_COLUMNS = {
    "is_spam": ("true_is_spam", SPAM_LABELS),
    "priority": ("true_priority", PRIORITY_LABELS),
    "needs_response": ("true_needs_response", RESPONSE_LABELS),
    "category": ("true_category", CATEGORY_LABELS),
}


def diagnose(
    input_path: Path,
    review_path: Path,
) -> dict[str, Any]:
    """Compare original and reviewed labels using aggregate counts only."""

    _, source_rows = read_csv(input_path)
    _, review_rows = read_csv(review_path)

    source_by_id = {
        (row.get("email_id") or "").strip(): row
        for row in source_rows
    }
    review_by_id = {
        (row.get("email_id") or "").strip(): row
        for row in review_rows
    }

    if len(source_by_id) != len(source_rows):
        raise ValueError(
            "Source CSV contains blank or duplicate email IDs."
        )

    if len(review_by_id) != len(review_rows):
        raise ValueError(
            "Review CSV contains blank or duplicate email IDs."
        )

    if not review_rows:
        raise ValueError("Review CSV contains no annotation rows.")

    missing = set(review_by_id) - set(source_by_id)
    if missing:
        raise ValueError(
            f"{len(missing)} review IDs are absent from the source CSV."
        )

    results: dict[str, Any] = {}

    for task, (column, valid_labels) in TASK_COLUMNS.items():
        pairs: Counter[tuple[str, str]] = Counter()
        matched = 0
        disagreed = 0
        unavailable = 0
        excluded_spam = 0

        for email_id, review_row in review_by_id.items():
            source_row = source_by_id[email_id]

            original, _ = _first_label(
                source_row,
                task,
                valid_labels,
            )
            reviewed = _norm(review_row.get(column), task)

            if task == "priority":
                source_spam, _ = _first_label(
                    source_row,
                    "is_spam",
                    SPAM_LABELS,
                )

                if source_spam == "yes":
                    excluded_spam += 1
                    continue

            if original not in valid_labels:
                unavailable += 1
                continue

            pairs[(original, reviewed)] += 1

            if original == reviewed:
                matched += 1
            else:
                disagreed += 1

        results[task] = {
            "paired_annotations": matched + disagreed,
            "matching_labels": matched,
            "disagreeing_labels": disagreed,
            "agreement_rate": (
                round(matched / (matched + disagreed), 4)
                if matched + disagreed
                else None
            ),
            "source_annotations_unavailable": unavailable,
            "excluded_source_spam_not_applicable": (
                excluded_spam if task == "priority" else 0
            ),
            "label_pair_counts": [
                {
                    "original_label": original,
                    "reviewed_label": reviewed,
                    "count": count,
                }
                for (original, reviewed), count in sorted(pairs.items())
            ],
        }

    return {
        "reviewed_emails": len(review_rows),
        "privacy_note": (
            "Aggregate label-pair counts only; no email IDs or message content."
        ),
        "tasks": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument(
        "--input",
        type=Path,
        default=Path("data/email_labels.csv"),
    )
    parser.add_argument(
        "--review",
        type=Path,
        default=Path("data/relabel_completed_repaired.csv"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/label_disagreement_diagnostics.json"),
    )

    args = parser.parse_args()

    try:
        result = diagnose(args.input, args.review)

        args.output.parent.mkdir(parents=True, exist_ok=True)

        if args.output.exists():
            raise FileExistsError(
                f"Output already exists: {args.output}. "
                "Choose another output path to avoid overwriting it."
            )

        temporary = args.output.with_suffix(
            args.output.suffix + ".tmp"
        )

        try:
            temporary.write_text(
                json.dumps(result, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary.replace(args.output)
        finally:
            if temporary.exists():
                temporary.unlink()

        print(json.dumps(result, indent=2))
        print(f"Aggregate diagnostics written to {args.output}")

    except (FileNotFoundError, ValueError, FileExistsError) as exc:
        parser.error(str(exc))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
