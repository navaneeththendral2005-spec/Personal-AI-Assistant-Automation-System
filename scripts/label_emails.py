"""Interactively label locally exported emails and save after every label.

Run: python -m scripts.label_emails --input data/email_labels.csv
Keyboard shortcuts: Enter=leave a field blank, q=quit safely.
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import Callable, TextIO
import sys

PRIORITIES = ("URGENT", "NEEDS_REPLY", "INFORMATIONAL", "SPAM")
NEEDS_RESPONSE = ("yes", "no")
CATEGORIES = ("JOB", "FINANCE", "PERSONAL", "PROMOTIONS", "UPDATES", "OTHER")
LABEL_FIELDS = ("true_priority", "true_needs_response", "true_category")


def load_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    """Load the CSV, adding required label columns to older exports in memory."""
    if not path.exists():
        raise FileNotFoundError(f"{path} not found. Run scripts/export_email_dataset.py first.")
    with path.open("r", newline="", encoding="utf-8-sig") as csv_file:
        reader = csv.DictReader(csv_file)
        if not reader.fieldnames or "email_id" not in reader.fieldnames:
            raise ValueError("CSV must contain an email_id column")
        fieldnames = list(reader.fieldnames)
        for field in LABEL_FIELDS:
            if field not in fieldnames:
                fieldnames.append(field)
        rows = []
        for source in reader:
            row = {field: (source.get(field) or "") for field in fieldnames}
            rows.append(row)
    return fieldnames, rows


def save_rows(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    """Atomically save the complete CSV to avoid losing previous labels."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with temp_path.open("w", newline="", encoding="utf-8-sig") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temp_path.replace(path)


def _ask_choice(prompt: str, choices: tuple[str, ...], input_fn: Callable[[str], str]) -> str | None:
    """Return a valid choice, blank, or None when the user requests quit."""
    while True:
        value = input_fn(f"{prompt} [{'/'.join(choices)}; Enter=blank; q=quit]: ").strip()
        if value.lower() == "q":
            return None
        if value == "":
            return ""
        normalized = value.upper() if choices[0].isupper() else value.lower()
        if normalized in choices:
            return normalized
        print(f"Please enter one of: {', '.join(choices)}, Enter, or q.")


def label_dataset(
    path: Path,
    *,
    input_fn: Callable[[str], str] = input,
    output: TextIO = sys.stdout,
    body_chars: int = 700,
) -> tuple[int, int]:
    """Label unlabeled rows interactively; save immediately after each email.

    Returns (number of emails completed this run, total fully labeled emails).
    An email is considered complete when all three target fields are nonblank.
    Existing field values are shown as defaults and can be retained with Enter.
    """
    if body_chars < 1:
        raise ValueError("body_chars must be positive")
    fieldnames, rows = load_rows(path)
    completed_this_run = 0

    for index, row in enumerate(rows, start=1):
        if all((row.get(field) or "").strip() for field in LABEL_FIELDS):
            continue
        body = (row.get("body_text") or row.get("snippet") or "").strip()
        if len(body) > body_chars:
            body = body[:body_chars].rstrip() + "… [truncated]"

        print("\n" + "=" * 72, file=output)
        print(f"Email {index}/{len(rows)} | ID: {row.get('email_id', '')}", file=output)
        print(f"Sender: {row.get('sender', '')}", file=output)
        print(f"Subject: {row.get('subject', '')}", file=output)
        print(f"Body: {body or '[No body/snippet available]'}", file=output)
        print("Current labels: " + ", ".join(f"{f}={row.get(f, '') or 'blank'}" for f in LABEL_FIELDS), file=output)
        print("Priority guide: URGENT=immediate/high-stakes; NEEDS_REPLY=someone should respond; INFORMATIONAL=FYI; SPAM=unwanted/malicious.", file=output)

        for field, choices in (("true_priority", PRIORITIES), ("true_needs_response", NEEDS_RESPONSE), ("true_category", CATEGORIES)):
            current = (row.get(field) or "").strip()
            if current:
                keep = input_fn(f"Keep {field}={current}? [Y/n/q]: ").strip().lower()
                if keep == "q":
                    save_rows(path, fieldnames, rows)
                    print("Progress saved; exiting.", file=output)
                    return completed_this_run, sum(all((r.get(f) or "").strip() for f in LABEL_FIELDS) for r in rows)
                if keep != "n":
                    continue
            value = _ask_choice(field, choices, input_fn)
            if value is None:
                save_rows(path, fieldnames, rows)
                print("Progress saved; exiting.", file=output)
                return completed_this_run, sum(all((r.get(f) or "").strip() for f in LABEL_FIELDS) for r in rows)
            row[field] = value

        if all((row.get(field) or "").strip() for field in LABEL_FIELDS):
            completed_this_run += 1
        save_rows(path, fieldnames, rows)
        print("Saved to CSV.", file=output)

    total_labeled = sum(all((r.get(f) or "").strip() for f in LABEL_FIELDS) for r in rows)
    print(f"Finished this pass: {completed_this_run} emails updated; {total_labeled}/{len(rows)} fully labeled.", file=output)
    return completed_this_run, total_labeled


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("data/email_labels.csv"))
    parser.add_argument("--body-chars", type=int, default=700)
    args = parser.parse_args()
    try:
        label_dataset(args.input, body_chars=args.body_chars)
    except (FileNotFoundError, ValueError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
