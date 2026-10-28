from __future__ import annotations

import csv
from pathlib import Path

SOURCE = Path("data/label_disagreement_adjudication.csv")
OUTPUT = Path("data/label_disagreement_adjudication_final.csv")

EXPECTED_HEADERS = [
    "email_id",
    "sender",
    "subject",
    "snippet",
    "body_text",
    "labels",
    "original_true_is_spam",
    "original_true_priority",
    "original_true_needs_response",
    "original_true_category",
    "reviewed_true_is_spam",
    "reviewed_true_priority",
    "reviewed_true_needs_response",
    "reviewed_true_category",
    "disagreement_fields",
    "final_true_is_spam",
    "final_true_priority",
    "final_true_needs_response",
    "final_true_category",
    "decision_reason",
]


def main() -> None:
    if not SOURCE.exists():
        raise FileNotFoundError(f"Source file not found: {SOURCE}")

    if OUTPUT.exists():
        raise FileExistsError(
            f"Output already exists: {OUTPUT}. "
            "Rename it before running this script again."
        )

    with SOURCE.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))

    if len(rows) < 2:
        raise ValueError("CSV does not contain enough rows.")

    first_row = [value.strip() for value in rows[0]]
    second_row = [value.strip() for value in rows[1]]

    if first_row == EXPECTED_HEADERS:
        # The source already has the correct header row.
        repaired_rows = [EXPECTED_HEADERS, *rows[1:]]

    elif second_row == EXPECTED_HEADERS:
        # Excel replaced the headers; restore them from the second row.
        repaired_rows = [EXPECTED_HEADERS, *rows[2:]]

    else:
        raise ValueError(
            "Neither the first nor second row matches the expected headers. "
            "No output was written; inspect the source CSV."
        )

    for line_number, row in enumerate(repaired_rows[1:], start=2):
        if len(row) != len(EXPECTED_HEADERS):
            raise ValueError(
                f"CSV row {line_number} has {len(row)} columns; "
                f"expected {len(EXPECTED_HEADERS)}. No output was written."
            )

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)

    with OUTPUT.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerows(repaired_rows)

    print("CSV headers validated/repaired successfully.")
    print(f"Data rows preserved: {len(repaired_rows) - 1}")
    print(f"Output: {OUTPUT}")
    print("Original CSV was not modified.")


if __name__ == "__main__":
    main()