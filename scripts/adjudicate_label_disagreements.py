from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import Any

from scripts.sample_test_v2 import read_csv

LABEL_COLUMNS = (
    "true_is_spam",
    "true_priority",
    "true_needs_response",
    "true_category",
)

CONTEXT_COLUMNS = (
    "email_id",
    "sender",
    "subject",
    "snippet",
    "body_text",
    "labels",
)


def normalize(value: str | None, column: str) -> str:
    """Normalize formatting differences without guessing label meaning."""
    value = (value or "").strip()

    if column == "true_is_spam" or column == "true_needs_response":
        return value.casefold()

    normalized = "_".join(value.upper().replace("-", " ").split())

    if column == "true_priority" and normalized == "NEEDSREPLY":
        return "NEEDS_REPLY"

    if column == "true_category" and normalized == "UPDATE":
        return "UPDATES"

    return normalized


def load_unique_rows(path: Path) -> dict[str, dict[str, str]]:
    """Load CSV rows indexed by unique, non-empty email ID."""
    _, rows = read_csv(path)
    indexed: dict[str, dict[str, str]] = {}

    for row in rows:
        email_id = (row.get("email_id") or "").strip()

        if not email_id:
            raise ValueError(f"CSV contains a row without an email_id: {path}")

        if email_id in indexed:
            raise ValueError(f"CSV contains duplicate email IDs: {path}")

        indexed[email_id] = row

    return indexed


def find_disagreements(
    original_rows: dict[str, dict[str, str]],
    reviewed_rows: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    """Build a separate adjudication row for each email with label differences."""
    unknown_ids = set(reviewed_rows) - set(original_rows)

    if unknown_ids:
        raise ValueError(
            f"The review CSV contains {len(unknown_ids)} email IDs "
            "that are absent from the original dataset."
        )

    results: list[dict[str, Any]] = []

    for email_id, reviewed in reviewed_rows.items():
        original = original_rows[email_id]
        disagreements: list[str] = []
        output: dict[str, Any] = {"email_id": email_id}

        for column in CONTEXT_COLUMNS:
            output[column] = original.get(column, "")

        for column in LABEL_COLUMNS:
            original_value = normalize(original.get(column), column)
            reviewed_value = normalize(reviewed.get(column), column)

            output[f"original_{column}"] = original_value
            output[f"reviewed_{column}"] = reviewed_value

            if original_value != reviewed_value:
                disagreements.append(column)

        if disagreements:
            output["disagreement_fields"] = ", ".join(disagreements)
            output["final_true_is_spam"] = ""
            output["final_true_priority"] = ""
            output["final_true_needs_response"] = ""
            output["final_true_category"] = ""
            output["decision_reason"] = ""
            results.append(output)

    return results


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    """Write the adjudication queue without overwriting an existing file."""
    if path.exists():
        raise FileExistsError(
            f"Output already exists: {path}. Choose a new output filename "
            "or move the existing file before retrying."
        )

    path.parent.mkdir(parents=True, exist_ok=True)

    fields = list(CONTEXT_COLUMNS) + [
        f"original_{column}" for column in LABEL_COLUMNS
    ] + [
        f"reviewed_{column}" for column in LABEL_COLUMNS
    ] + [
        "disagreement_fields",
        "final_true_is_spam",
        "final_true_priority",
        "final_true_needs_response",
        "final_true_category",
        "decision_reason",
    ]

    temp = path.with_suffix(path.suffix + ".tmp")

    try:
        with temp.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=fields,
                extrasaction="ignore",
            )
            writer.writeheader()
            writer.writerows(rows)

        # Never replace an existing destination.
        if path.exists():
            raise FileExistsError(f"Output already exists: {path}")

        temp.rename(path)

    finally:
        if temp.exists():
            temp.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument(
        "--input",
        type=Path,
        default=Path("data/email_labels.csv"),
        help="Original annotated dataset.",
    )
    parser.add_argument(
        "--review",
        type=Path,
        default=Path("data/relabel_completed_repaired.csv"),
        help="Completed independent review CSV.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/label_disagreement_adjudication.csv"),
        help="New private adjudication CSV.",
    )

    args = parser.parse_args()

    try:
        original_rows = load_unique_rows(args.input)
        reviewed_rows = load_unique_rows(args.review)

        disagreements = find_disagreements(original_rows, reviewed_rows)
        write_csv(args.output, disagreements)

    except (FileNotFoundError, ValueError, FileExistsError) as exc:
        parser.error(str(exc))

    print("Disagreement review queue created successfully.")
    print(f"Emails reviewed: {len(reviewed_rows)}")
    print(f"Emails with disagreements: {len(disagreements)}")
    print(f"Output: {args.output}")
    print("Input datasets were not modified.")
    print("Keep the adjudication CSV private; it contains email context.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
