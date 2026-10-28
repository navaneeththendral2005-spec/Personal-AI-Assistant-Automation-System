from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any, Sequence

DEFAULT_INPUT = Path("data/label_disagreement_adjudication_final.csv")
DEFAULT_OUTPUT = Path("reports/label_disagreement_adjudication_final.json")

VALID_LABELS = {
    "final_true_is_spam": {"yes", "no"},
    "final_true_priority": {"URGENT", "NEEDS_REPLY", "INFORMATIONAL"},
    "final_true_needs_response": {"yes", "no"},
    "final_true_category": {
        "JOB", "FINANCE", "PERSONAL", "PROMOTIONS", "UPDATES", "OTHER",
    },
}

REQUIRED_COLUMNS = {
    "email_id",
    "final_true_is_spam",
    "final_true_priority",
    "final_true_needs_response",
    "final_true_category",
    "decision_reason",
}


def normalize(value: str | None, column: str) -> str:
    """Normalize label formatting without guessing the intended label."""
    value = (value or "").strip()

    if column in {"final_true_is_spam", "final_true_needs_response"}:
        return value.casefold()

    normalized = "_".join(value.upper().replace("-", " ").split())

    if column == "final_true_priority" and normalized == "NEEDSREPLY":
        return "NEEDS_REPLY"

    if column == "final_true_category" and normalized == "UPDATE":
        return "UPDATES"

    return normalized


def validate_rows(rows: list[dict[str, str]]) -> dict[str, Any]:
    """Return aggregate validation results without exposing email content."""
    if not rows:
        raise ValueError("Adjudication CSV contains no email rows.")

    seen_ids: set[str] = set()
    invalid_counts: Counter[str] = Counter()
    missing_counts: Counter[str] = Counter()
    spam_priority_conflicts = 0
    missing_reasons = 0
    spam_rows = 0

    for row in rows:
        email_id = (row.get("email_id") or "").strip()
        if not email_id:
            raise ValueError("A row has a missing email ID.")
        if email_id in seen_ids:
            raise ValueError("Duplicate email IDs found in adjudication CSV.")
        seen_ids.add(email_id)

        labels = {
            column: normalize(row.get(column), column)
            for column in VALID_LABELS
        }
        spam = labels["final_true_is_spam"]
        priority = labels["final_true_priority"]

        if spam == "yes":
            spam_rows += 1
            if priority:
                spam_priority_conflicts += 1

        for column, allowed in VALID_LABELS.items():
            value = labels[column]
            # Priority is intentionally blank for spam emails.
            if column == "final_true_priority" and spam == "yes":
                continue
            if not value:
                missing_counts[column] += 1
            elif value not in allowed:
                invalid_counts[column] += 1

        if not (row.get("decision_reason") or "").strip():
            missing_reasons += 1

    invalid_total = sum(invalid_counts.values())
    missing_total = sum(missing_counts.values())
    passed = (
        invalid_total == 0
        and missing_total == 0
        and spam_priority_conflicts == 0
        and missing_reasons == 0
    )

    return {
        "reviewed_emails": len(rows),
        "validation_status": "passed" if passed else "failed",
        "privacy_note": (
            "Aggregate counts only. No email IDs, senders, subjects, "
            "or message content included."
        ),
        "invalid_values_by_field": dict(invalid_counts),
        "missing_values_by_field": dict(missing_counts),
        "spam_priority_conflicts": spam_priority_conflicts,
        "missing_decision_reasons": missing_reasons,
        "spam_email_count": spam_rows,
    }


def run_validation(input_path: Path, output_path: Path) -> dict[str, Any]:
    """Validate a CSV, write an aggregate report, and return the report."""
    if not input_path.exists():
        raise FileNotFoundError(f"Adjudication CSV not found: {input_path}")

    with input_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        headers = set(reader.fieldnames or [])
        missing_columns = sorted(REQUIRED_COLUMNS - headers)
        if missing_columns:
            raise ValueError(
                "CSV headers are missing required fields: "
                + ", ".join(missing_columns)
                + ". Ensure the actual header row is the first row in the CSV."
            )
        rows = list(reader)

    report = validate_rows(rows)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp = output_path.with_suffix(output_path.suffix + ".tmp")
    try:
        temp.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        temp.replace(output_path)
    finally:
        if temp.exists():
            temp.unlink()
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input", type=Path, default=DEFAULT_INPUT,
        help=f"Adjudication CSV (default: {DEFAULT_INPUT}).",
    )
    parser.add_argument(
        "--output", type=Path, default=DEFAULT_OUTPUT,
        help=f"Aggregate JSON report (default: {DEFAULT_OUTPUT}).",
    )
    args = parser.parse_args(argv)

    report = run_validation(args.input, args.output)
    print(json.dumps(report, indent=2))
    print(f"Aggregate validation report written to {args.output}")
    return 0 if report["validation_status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
