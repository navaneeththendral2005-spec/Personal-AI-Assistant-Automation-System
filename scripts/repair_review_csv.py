"""Repair review CSV headers/schema from the intact original review file.

Reads data/relabel_completed_fixed.csv and writes
 data/relabel_completed_repaired.csv without modifying the source.

Rules follow EMAIL_LABELING_GUIDE.md:
- Spam=yes => true_priority is blank.
- Non-spam legacy true_priority=SPAM is changed to INFORMATIONAL only because
  the user explicitly reviewed that one conflicting row and selected
  INFORMATIONAL in the repaired spreadsheet.
- Formatting aliases are normalized (e.g. "needs reply" -> NEEDS_REPLY,
  "update" -> UPDATES).
- No email text, sender, or identifiers are printed.
"""

from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INPUT_PATH = ROOT / "data" / "relabel_completed_fixed.csv"
OUTPUT_PATH = ROOT / "data" / "relabel_completed_repaired.csv"

REQUIRED_COLUMNS = {
    "email_id",
    "sender",
    "subject",
    "snippet",
    "body_text",
    "labels",
    "true_is_spam",
    "true_priority",
    "true_needs_response",
    "true_category",
}
VALID_SPAM = {"yes", "no"}
VALID_PRIORITY = {"URGENT", "NEEDS_REPLY", "INFORMATIONAL"}
VALID_RESPONSE = {"yes", "no"}
VALID_CATEGORY = {"JOB", "FINANCE", "PERSONAL", "PROMOTIONS", "UPDATES", "OTHER"}


def normalize_priority(value: str) -> str:
    value = value.strip().upper().replace(" ", "_")
    aliases = {"NEEDS_REPLY": "NEEDS_REPLY"}
    return aliases.get(value, value)


def normalize_category(value: str) -> str:
    value = value.strip().upper()
    aliases = {"UPDATE": "UPDATES"}
    return aliases.get(value, value)


def main() -> None:
    if not INPUT_PATH.is_file():
        raise SystemExit(
            "Source review CSV not found: data/relabel_completed_fixed.csv. "
            "Keep the original file in the project's data folder."
        )
    if OUTPUT_PATH.exists():
        raise SystemExit(
            "Output already exists: data/relabel_completed_repaired.csv. "
            "Move/rename it before running again; this script never overwrites files."
        )

    with INPUT_PATH.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        if not reader.fieldnames:
            raise SystemExit("Source CSV has no header.")
        missing = REQUIRED_COLUMNS.difference(reader.fieldnames)
        if missing:
            raise SystemExit(
                "Source CSV is missing required columns: " + ", ".join(sorted(missing))
            )
        fieldnames = list(reader.fieldnames)
        rows = list(reader)

    if len(rows) != 28:
        raise SystemExit(
            f"Expected 28 reviewed email rows, found {len(rows)}. "
            "Refusing to guess which rows to repair."
        )

    # The original file has exactly one explicit non-spam row with legacy SPAM
    # priority. The user reviewed this row in Excel and changed it to INFORMATIONAL.
    nonspam_legacy_spam = [
        row for row in rows
        if row["true_is_spam"].strip().lower() == "no"
        and normalize_priority(row["true_priority"]) == "SPAM"
    ]
    if len(nonspam_legacy_spam) != 1:
        raise SystemExit(
            "Expected exactly one non-spam row with legacy SPAM priority; "
            f"found {len(nonspam_legacy_spam)}. No output was written."
        )

    cleared_spam_priorities = 0
    converted_legacy_priority = 0

    for row in rows:
        spam = row["true_is_spam"].strip().lower()
        if spam not in VALID_SPAM:
            raise SystemExit("Invalid true_is_spam value found. No output was written.")
        row["true_is_spam"] = spam

        priority = normalize_priority(row["true_priority"])
        if spam == "yes":
            if row["true_priority"].strip():
                cleared_spam_priorities += 1
            row["true_priority"] = ""
        elif priority == "SPAM":
            # Explicitly follows the user's confirmed manual correction.
            row["true_priority"] = "INFORMATIONAL"
            converted_legacy_priority += 1
        else:
            row["true_priority"] = priority

        response = row["true_needs_response"].strip().lower()
        if response not in VALID_RESPONSE:
            raise SystemExit("Invalid true_needs_response value found. No output was written.")
        row["true_needs_response"] = response

        category = normalize_category(row["true_category"])
        if category not in VALID_CATEGORY:
            raise SystemExit("Invalid true_category value found. No output was written.")
        row["true_category"] = category

        if row["true_priority"] and row["true_priority"] not in VALID_PRIORITY:
            raise SystemExit("Invalid non-spam priority remains. No output was written.")
        if spam == "no" and not row["true_priority"]:
            raise SystemExit("A non-spam row has blank priority. No output was written.")

    with OUTPUT_PATH.open("x", encoding="utf-8-sig", newline="") as destination:
        writer = csv.DictWriter(destination, fieldnames=fieldnames, extrasaction="raise")
        writer.writeheader()
        writer.writerows(rows)

    priority_counts = Counter(row["true_priority"] or "<BLANK>" for row in rows)
    category_counts = Counter(row["true_category"] for row in rows)
    print("Review CSV repaired and validated.")
    print(f"Rows: {len(rows)}")
    print(f"Spam priorities cleared: {cleared_spam_priorities}")
    print(f"Reviewed legacy priority converted to INFORMATIONAL: {converted_legacy_priority}")
    print(f"Priority counts: {dict(sorted(priority_counts.items()))}")
    print(f"Category counts: {dict(sorted(category_counts.items()))}")
    print("Output: data/relabel_completed_repaired.csv")
    print("Source file was not modified. No email content or IDs were printed.")


if __name__ == "__main__":
    main()
