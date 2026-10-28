"""Finalize a completed FRIDAY email-label review CSV without overwriting inputs.

Run from the project root:
    python -m scripts.finalize_review_csv

By default, reads data/relabel_completed_schema_fixed.csv and writes
data/relabel_completed_repaired.csv. Existing files are never overwritten.
"""
from __future__ import annotations

import csv
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path.cwd()
DEFAULT_INPUT = PROJECT_ROOT / "data" / "relabel_completed_schema_fixed.csv"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "relabel_completed_repaired.csv"

REQUIRED_COLUMNS = (
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
)

SPAM_VALUES = {"yes", "no"}
PRIORITY_ALIASES = {
    "urgent": "URGENT",
    "needs reply": "NEEDS_REPLY",
    "needs_reply": "NEEDS_REPLY",
    "needs-reply": "NEEDS_REPLY",
    "informational": "INFORMATIONAL",
}
CATEGORY_ALIASES = {
    "job": "JOB",
    "finance": "FINANCE",
    "personal": "PERSONAL",
    "promotions": "PROMOTIONS",
    "promotion": "PROMOTIONS",
    "update": "UPDATES",
    "updates": "UPDATES",
    "other": "OTHER",
}
YES_NO_ALIASES = {
    "yes": "yes",
    "y": "yes",
    "true": "yes",
    "no": "no",
    "n": "no",
    "false": "no",
}


def read_review_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    """Read a normal CSV or an Excel-exported CSV with a generic first row."""
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))

    if not rows:
        raise ValueError(f"Input CSV is empty: {path}")

    # Excel sometimes promotes generic Column1...Column10 labels to the CSV
    # header and leaves the actual field names in the first data row.
    first = [cell.strip() for cell in rows[0]]
    if first and all(
        value.casefold() == f"column{index}".casefold()
        for index, value in enumerate(first, start=1)
    ):
        rows = rows[1:]
        if not rows:
            raise ValueError("CSV contains generic headers but no actual header row.")

    headers = [cell.strip().lstrip("\ufeff") for cell in rows[0]]
    if len(headers) != len(set(headers)):
        raise ValueError("Input CSV has duplicate column names.")

    missing = sorted(set(REQUIRED_COLUMNS) - set(headers))
    if missing:
        raise ValueError(
            "Input CSV is missing required columns: " + ", ".join(missing)
        )

    records: list[dict[str, str]] = []
    for line_number, values in enumerate(rows[1:], start=2):
        if not values or all(not value.strip() for value in values):
            continue
        if len(values) != len(headers):
            raise ValueError(
                f"CSV row {line_number} has {len(values)} cells; "
                f"expected {len(headers)}. No output was written."
            )
        records.append(dict(zip(headers, values)))

    return headers, records


def normalize_yes_no(value: str, field: str, row_number: int) -> str:
    normalized = value.strip().casefold()
    result = YES_NO_ALIASES.get(normalized)
    if result is None:
        raise ValueError(
            f"Review row {row_number}: {field} must be yes/no; "
            "no output was written."
        )
    return result


def normalize_record(record: dict[str, str], row_number: int) -> dict[str, str]:
    result = dict(record)

    result["true_is_spam"] = normalize_yes_no(
        result.get("true_is_spam", ""), "true_is_spam", row_number
    )
    result["true_needs_response"] = normalize_yes_no(
        result.get("true_needs_response", ""),
        "true_needs_response",
        row_number,
    )

    priority_raw = result.get("true_priority", "").strip()
    priority_key = priority_raw.casefold()
    spam = result["true_is_spam"]

    if spam == "yes":
        # The new schema leaves priority blank for spam. This intentionally
        # replaces only the obsolete SPAM priority representation.
        if priority_key in {"spam", ""}:
            result["true_priority"] = ""
        else:
            raise ValueError(
                f"Review row {row_number}: spam=yes but priority is "
                f"{priority_raw!r}. Review the annotation; no output was written."
            )
    else:
        priority = PRIORITY_ALIASES.get(priority_key)
        if priority is None:
            raise ValueError(
                f"Review row {row_number}: non-spam priority "
                f"{priority_raw!r} is invalid or blank. Allowed: URGENT, "
                "NEEDS_REPLY, INFORMATIONAL. No output was written."
            )
        result["true_priority"] = priority

    category_raw = result.get("true_category", "").strip()
    category = CATEGORY_ALIASES.get(category_raw.casefold())
    if category is None:
        raise ValueError(
            f"Review row {row_number}: invalid true_category "
            f"{category_raw!r}. No output was written."
        )
    result["true_category"] = category

    # Preserve text and all non-target columns exactly as supplied.
    return result


def main() -> int:
    input_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_INPUT
    output_path = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_OUTPUT

    if not input_path.exists():
        print(f"ERROR: input file not found: {input_path}", file=sys.stderr)
        return 2
    if output_path.exists():
        print(
            f"ERROR: output already exists: {output_path}\n"
            "Nothing was overwritten. Choose a new output path as the second argument.",
            file=sys.stderr,
        )
        return 2

    try:
        headers, records = read_review_csv(input_path)
        if len(records) != 28:
            raise ValueError(
                f"Expected 28 reviewed email rows, found {len(records)}. "
                "No output was written."
            )

        normalized = [
            normalize_record(record, row_number=index + 1)
            for index, record in enumerate(records)
        ]

        # Final schema checks before writing anything.
        for index, record in enumerate(normalized, start=1):
            if record["true_is_spam"] == "yes" and record["true_priority"]:
                raise ValueError(
                    f"Review row {index}: spam priority must be blank."
                )

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("x", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(
                handle, fieldnames=headers, extrasaction="raise", lineterminator="\n"
            )
            writer.writeheader()
            writer.writerows(normalized)

    except (OSError, ValueError, csv.Error) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print("Review CSV finalized successfully.")
    print(f"Input: {input_path}")
    print(f"Output: {output_path}")
    print(f"Rows written: {len(normalized)}")

    for column in (
        "true_is_spam",
        "true_priority",
        "true_needs_response",
        "true_category",
    ):
        counts = Counter(
            (record.get(column) or "").strip() or "<BLANK>"
            for record in normalized
        )
        print(f"{column}: {dict(counts)}")

    print("Validation: all labels valid; spam priorities blank; no input overwritten.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
