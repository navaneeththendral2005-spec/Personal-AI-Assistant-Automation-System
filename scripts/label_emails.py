"""Interactively label locally exported emails and save after every email.

Run:
    python -m scripts.label_emails --input data/email_labels.csv
    python -m scripts.label_emails --input data/email_labels.csv --queue data/email_review_queue.csv
    python -m scripts.label_emails --input data/email_labels.csv --limit 10

Keyboard shortcuts:
    Enter = leave a new field blank
    q     = quit safely, saving progress

Ground-truth fields use true_* names. Predictions are suggestions only.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from typing import Callable, TextIO

YES_NO = ("yes", "no")
PRIORITIES = ("URGENT", "NEEDS_REPLY", "INFORMATIONAL")
LEGACY_PRIORITIES = (*PRIORITIES, "SPAM")
NEEDS_RESPONSE = YES_NO
CATEGORIES = ("JOB", "FINANCE", "PERSONAL", "PROMOTIONS", "UPDATES", "OTHER")
LABEL_FIELDS = ("true_is_spam", "true_priority", "true_needs_response", "true_category")
LEGACY_LABEL_FIELDS = ("true_priority", "true_needs_response", "true_category")


def _source_has_spam_column(path: Path) -> bool:
    """Distinguish new-schema files from legacy CSVs to preserve old workflows."""
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.reader(handle)
        header = next(reader, [])
    return "true_is_spam" in header


def load_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    """Load the CSV and add missing target-label columns without dropping data."""
    if not path.is_file():
        raise FileNotFoundError(
            f"{path} not found. Run scripts/export_email_dataset.py first."
        )
    with path.open("r", newline="", encoding="utf-8-sig") as csv_file:
        reader = csv.DictReader(csv_file)
        if not reader.fieldnames or "email_id" not in reader.fieldnames:
            raise ValueError("CSV must contain an email_id column")
        fieldnames = list(reader.fieldnames)
        for field in LABEL_FIELDS:
            if field not in fieldnames:
                fieldnames.append(field)
        rows = [
            {field: (source.get(field) or "") for field in fieldnames}
            for source in reader
        ]

    # Only infer the new spam field from an existing human label, never from predictions.
    for row in rows:
        if not (row.get("true_is_spam") or "").strip():
            old_priority = (row.get("true_priority") or "").strip()
            if old_priority == "SPAM":
                row["true_is_spam"] = "yes"
            elif old_priority in PRIORITIES:
                row["true_is_spam"] = "no"
    return fieldnames, rows


def save_rows(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    """Atomically save the CSV without leaving a temporary file behind."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        with temp_path.open("w", newline="", encoding="utf-8-sig") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        temp_path.replace(path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def _ask_choice(
    prompt: str,
    choices: tuple[str, ...],
    input_fn: Callable[[str], str],
    output: TextIO = sys.stdout,
) -> str | None:
    """Return a valid choice, blank, or None when quitting."""
    while True:
        value = input_fn(f"{prompt} [{'/'.join(choices)}; Enter=blank; q=quit]: ").strip()
        if value.lower() == "q":
            return None
        if value == "":
            return ""
        normalized = value.upper() if choices[0].isupper() else value.lower()
        if normalized in choices:
            return normalized
        print(f"Please enter one of: {', '.join(choices)}, Enter, or q.", file=output)


def _fully_labeled(row: dict[str, str]) -> bool:
    """Check completeness under the split schema, allowing historical SPAM rows."""
    spam = (row.get("true_is_spam") or "").strip()
    priority = (row.get("true_priority") or "").strip()
    if spam not in YES_NO:
        return False
    if spam == "no" and priority not in PRIORITIES:
        return False
    if spam == "yes" and priority not in ("", "SPAM"):
        return False
    return (
        (row.get("true_needs_response") or "").strip() in NEEDS_RESPONSE
        and (row.get("true_category") or "").strip() in CATEGORIES
    )


def _legacy_fully_labeled(row: dict[str, str]) -> bool:
    return (
        (row.get("true_priority") or "").strip() in LEGACY_PRIORITIES
        and (row.get("true_needs_response") or "").strip() in NEEDS_RESPONSE
        and (row.get("true_category") or "").strip() in CATEGORIES
    )


def _load_queue_ids(queue_path: Path) -> set[str]:
    """Load email IDs from a review queue CSV."""
    if not queue_path.is_file():
        raise FileNotFoundError(f"Review queue not found: {queue_path}")
    with queue_path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or "email_id" not in reader.fieldnames:
            raise ValueError("Review queue CSV must contain an email_id column")
        return {(row.get("email_id") or "").strip() for row in reader if (row.get("email_id") or "").strip()}


def _quit(path: Path, fields: list[str], rows: list[dict[str, str]], done: int,
          output: TextIO, *, legacy: bool) -> tuple[int, int]:
    save_rows(path, fields, rows)
    complete = sum(_legacy_fully_labeled(r) if legacy else _fully_labeled(r) for r in rows)
    print("Progress saved; exiting.", file=output)
    return done, complete


def label_dataset(
    path: Path,
    *,
    input_fn: Callable[[str], str] = input,
    output: TextIO = sys.stdout,
    body_chars: int = 700,
    queue_path: Path | None = None,
    limit: int | None = None,
) -> tuple[int, int]:
    """Label incomplete emails; save after each email and on quit.

    Files that already contain true_is_spam use the split schema. Older CSVs
    retain the legacy prompt order until explicitly migrated, keeping existing
    users' workflows and tests compatible.
    """
    if body_chars < 1:
        raise ValueError("body_chars must be positive")
    if limit is not None and limit < 1:
        raise ValueError("limit must be positive")

    if not path.is_file():
        raise FileNotFoundError(f"{path} not found. Run scripts/export_email_dataset.py first.")
    split_schema = _source_has_spam_column(path)
    fields, rows = load_rows(path)
    legacy = not split_schema

    # load_rows exposes the additive field for migration/testing, but do not
    # persist it when operating in legacy mode. Otherwise the next run would
    # mistake a legacy CSV for the split schema and change its prompt order.
    if legacy:
        fields = [field for field in fields if field != "true_is_spam"]

    complete_fn = _legacy_fully_labeled if legacy else _fully_labeled
    selected = _load_queue_ids(queue_path) if queue_path is not None else None
    targets = [
        (index, row) for index, row in enumerate(rows, start=1)
        if not complete_fn(row) and (selected is None or (row.get("email_id") or "").strip() in selected)
    ]
    if limit is not None:
        targets = targets[:limit]

    done = 0
    for index, row in targets:
        body = (row.get("body_text") or row.get("snippet") or "").strip()
        if len(body) > body_chars:
            body = body[:body_chars].rstrip() + "… [truncated]"
        print("\n" + "=" * 72, file=output)
        print(f"Email {index}/{len(rows)} | ID: {row.get('email_id', '')}", file=output)
        print(f"Sender: {row.get('sender', '')}", file=output)
        print(f"Subject: {row.get('subject', '')}", file=output)
        print(f"Body: {body or '[No body/snippet available]'}", file=output)

        visible_fields = LABEL_FIELDS if not legacy else LEGACY_LABEL_FIELDS
        print("Current labels: " + ", ".join(
            f"{f}={row.get(f, '') or 'blank'}" for f in visible_fields
        ), file=output)
        suggestion_fields = (
            ("predicted_is_spam", "predicted_priority", "predicted_needs_response", "predicted_category")
            if not legacy else
            ("predicted_priority", "predicted_needs_response", "predicted_category")
        )
        suggestions = [f"{f}={row.get(f)}" for f in suggestion_fields if (row.get(f) or "").strip()]
        if suggestions:
            print("Provisional suggestions (verify; not ground truth): " + ", ".join(suggestions), file=output)

        if legacy:
            print("Priority guide: URGENT=immediate/high-stakes; NEEDS_REPLY=someone should respond; INFORMATIONAL=FYI; SPAM=unwanted/malicious.", file=output)
            fields_to_label = (
                ("true_priority", LEGACY_PRIORITIES),
                ("true_needs_response", NEEDS_RESPONSE),
                ("true_category", CATEGORIES),
            )
        else:
            print("Decision order: 1) spam? 2) if not spam, priority; 3) needs response; 4) category. Spam and priority are separate labels.", file=output)
            # Spam is collected explicitly below; do not ask for it a second time.
            fields_to_label = ()

        # For split schema, collect spam first, then priority only for non-spam.
        if not legacy:
            current = (row.get("true_is_spam") or "").strip()
            if current in YES_NO:
                keep = input_fn(f"Keep true_is_spam={current}? [Y/n/q]: ").strip().lower()
                if keep == "q":
                    return _quit(path, fields, rows, done, output, legacy=legacy)
                if keep == "n":
                    value = _ask_choice("true_is_spam", YES_NO, input_fn, output)
                    if value is None:
                        return _quit(path, fields, rows, done, output, legacy=legacy)
                    row["true_is_spam"] = value
            else:
                value = _ask_choice("true_is_spam", YES_NO, input_fn, output)
                if value is None:
                    return _quit(path, fields, rows, done, output, legacy=legacy)
                row["true_is_spam"] = value

            if row["true_is_spam"] == "no":
                current = (row.get("true_priority") or "").strip()
                if current in PRIORITIES:
                    keep = input_fn(f"Keep true_priority={current}? [Y/n/q]: ").strip().lower()
                    if keep == "q":
                        return _quit(path, fields, rows, done, output, legacy=legacy)
                    if keep == "n":
                        value = _ask_choice("true_priority", PRIORITIES, input_fn, output)
                        if value is None:
                            return _quit(path, fields, rows, done, output, legacy=legacy)
                        row["true_priority"] = value
                else:
                    if current == "SPAM":
                        print("Legacy true_priority=SPAM retained; choose a non-spam priority only if you explicitly correct the spam label.", file=output)
                    value = _ask_choice("true_priority", PRIORITIES, input_fn, output)
                    if value is None:
                        return _quit(path, fields, rows, done, output, legacy=legacy)
                    row["true_priority"] = value
            elif row["true_is_spam"] == "yes":
                # Never silently discard an existing human priority annotation.
                # If it conflicts with spam=yes, ask before clearing it.
                current_priority = (row.get("true_priority") or "").strip()
                if current_priority in PRIORITIES:
                    clear = input_fn(
                        f"Clear true_priority={current_priority} because this email "
                        "is marked spam? [y/N/q]: "
                    ).strip().lower()
                    if clear == "q":
                        return _quit(path, fields, rows, done, output, legacy=legacy)
                    if clear == "y":
                        row["true_priority"] = ""
                    else:
                        print(
                            "Warning: spam=yes conflicts with the existing priority; "
                            "the row will remain incomplete until resolved.",
                            file=output,
                        )

            fields_to_label += (
                ("true_needs_response", NEEDS_RESPONSE),
                ("true_category", CATEGORIES),
            )

        for field, choices in fields_to_label:
            current = (row.get(field) or "").strip()
            if current in choices:
                keep = input_fn(f"Keep {field}={current}? [Y/n/q]: ").strip().lower()
                if keep == "q":
                    return _quit(path, fields, rows, done, output, legacy=legacy)
                if keep != "n":
                    continue
            elif current:
                print(f"Existing value {current!r} is invalid for {field}; please correct it.", file=output)
            value = _ask_choice(field, choices, input_fn, output)
            if value is None:
                return _quit(path, fields, rows, done, output, legacy=legacy)
            row[field] = value

        if complete_fn(row):
            done += 1
        save_rows(path, fields, rows)
        print("Saved to CSV.", file=output)

    total = sum(complete_fn(row) for row in rows)
    print(f"Finished this pass: {done} emails completed; {total}/{len(rows)} fully labeled.", file=output)
    return done, total


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("data/email_labels.csv"))
    parser.add_argument("--body-chars", type=int, default=700)
    parser.add_argument("--queue", type=Path, help="Only label email IDs in this review-queue CSV")
    parser.add_argument("--limit", type=int, help="Maximum number of incomplete emails to process")
    args = parser.parse_args()
    try:
        label_dataset(args.input, body_chars=args.body_chars, queue_path=args.queue, limit=args.limit)
    except (FileNotFoundError, ValueError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
