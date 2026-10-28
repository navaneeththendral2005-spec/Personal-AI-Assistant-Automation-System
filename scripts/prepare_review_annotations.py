import csv
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

INPUT_FILE = PROJECT_ROOT / "data" / "relabel_completed_fixed.csv"
OUTPUT_FILE = PROJECT_ROOT / "data" / "relabel_completed_schema_fixed.csv"

VALID_SPAM_LABELS = {"yes", "no"}
VALID_NONSPAM_PRIORITIES = {
    "URGENT",
    "NEEDS_REPLY",
    "INFORMATIONAL",
}

def normalize_priority(value: str) -> str:
    """Normalize formatting without changing the label's meaning."""
    normalized = value.strip().upper().replace(" ", "_")
    return normalized


def main() -> None:
    if not INPUT_FILE.exists():
        raise FileNotFoundError(f"Review file not found: {INPUT_FILE}")

    if OUTPUT_FILE.exists():
        raise FileExistsError(
            f"Output already exists: {OUTPUT_FILE}\n"
            "Rename or move it before running this script again."
        )

    with INPUT_FILE.open(
        "r", encoding="utf-8-sig", newline=""
    ) as source:
        reader = csv.DictReader(source)

        if not reader.fieldnames:
            raise ValueError("The review CSV has no header.")

        required = {
            "true_is_spam",
            "true_priority",
            "true_needs_response",
            "true_category",
        }
        missing = required.difference(reader.fieldnames)

        if missing:
            raise ValueError(
                "Review CSV is missing required columns: "
                + ", ".join(sorted(missing))
            )

        fieldnames = reader.fieldnames
        rows = list(reader)

    cleared_spam_priorities = 0
    manual_review_rows = []

    for row_number, row in enumerate(rows, start=2):
        spam = row["true_is_spam"].strip().lower()
        priority = normalize_priority(row["true_priority"])

        if spam not in VALID_SPAM_LABELS:
            raise ValueError(
                f"Invalid spam annotation at spreadsheet row {row_number}."
            )

        if spam == "yes":
            if row["true_priority"].strip():
                row["true_priority"] = ""
                cleared_spam_priorities += 1
            continue

        # A legacy SPAM priority conflicts with an explicit non-spam label.
        # Do not guess the correct priority; require human review.
        if priority == "SPAM":
            manual_review_rows.append(row_number)
            continue

        if priority not in VALID_NONSPAM_PRIORITIES:
            raise ValueError(
                f"Invalid non-spam priority at spreadsheet row {row_number}."
            )

        # Normalize harmless formatting differences.
        row["true_priority"] = priority

    with OUTPUT_FILE.open(
        "x", encoding="utf-8-sig", newline=""
    ) as destination:
        writer = csv.DictWriter(
            destination,
            fieldnames=fieldnames,
            extrasaction="raise",
        )
        writer.writeheader()
        writer.writerows(rows)

    print("Review annotation preparation complete.")
    print(f"Input rows: {len(rows)}")
    print(f"Spam priorities cleared: {cleared_spam_priorities}")
    print(f"Rows requiring manual priority review: {len(manual_review_rows)}")
    print(f"Output: {OUTPUT_FILE.relative_to(PROJECT_ROOT)}")

    if manual_review_rows:
        print(
            "Spreadsheet row numbers requiring manual review: "
            + ", ".join(map(str, manual_review_rows))
        )
        print(
            "Do not score this file until these conflicts are resolved."
        )
    else:
        print("No spam/priority conflicts remain.")

if __name__ == "__main__":
    main()
