"""Interactively label locally exported emails and save after every email.

Run:
    python -m scripts.label_emails --input data/email_labels.csv
    python -m scripts.label_emails --input data/email_labels.csv --queue data/email_review_queue.csv
    python -m scripts.label_emails --input data/email_labels.csv --limit 10

Keyboard shortcuts:
    Enter = leave a new field blank
    q     = quit safely, saving progress
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from typing import Callable, TextIO

PRIORITIES = ("URGENT", "NEEDS_REPLY", "INFORMATIONAL", "SPAM")
NEEDS_RESPONSE = ("yes", "no")
CATEGORIES = ("JOB", "FINANCE", "PERSONAL", "PROMOTIONS", "UPDATES", "OTHER")
LABEL_FIELDS = ("true_priority", "true_needs_response", "true_category")


def load_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    """Load the CSV and add missing target-label columns."""
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

    return fieldnames, rows


def save_rows(
    path: Path,
    fieldnames: list[str],
    rows: list[dict[str, str]],
) -> None:
    """Atomically save the CSV without leaving a temporary file behind."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")

    try:
        with temp_path.open(
            "w", newline="", encoding="utf-8-sig"
        ) as csv_file:
            writer = csv.DictWriter(
                csv_file,
                fieldnames=fieldnames,
                extrasaction="ignore",
            )
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
    """Return a valid choice, a blank value, or None when quitting."""
    while True:
        value = input_fn(
            f"{prompt} [{'/'.join(choices)}; Enter=blank; q=quit]: "
        ).strip()

        if value.lower() == "q":
            return None

        if value == "":
            return ""

        normalized = value.upper() if choices[0].isupper() else value.lower()

        if normalized in choices:
            return normalized

        print(
            f"Please enter one of: {', '.join(choices)}, Enter, or q.",
            file=output,
        )


def _fully_labeled(row: dict[str, str]) -> bool:
    """Whether all target labels are present and valid."""
    allowed = {
        "true_priority": PRIORITIES,
        "true_needs_response": NEEDS_RESPONSE,
        "true_category": CATEGORIES,
    }

    return all(
        (row.get(field) or "").strip() in allowed[field]
        for field in LABEL_FIELDS
    )


def _load_queue_ids(queue_path: Path) -> set[str]:
    """Load email IDs from a review queue CSV."""
    if not queue_path.is_file():
        raise FileNotFoundError(f"Review queue not found: {queue_path}")

    with queue_path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)

        if not reader.fieldnames or "email_id" not in reader.fieldnames:
            raise ValueError(
                "Review queue CSV must contain an email_id column"
            )

        return {
            (row.get("email_id") or "").strip()
            for row in reader
            if (row.get("email_id") or "").strip()
        }


def label_dataset(
    path: Path,
    *,
    input_fn: Callable[[str], str] = input,
    output: TextIO = sys.stdout,
    body_chars: int = 700,
    queue_path: Path | None = None,
    limit: int | None = None,
) -> tuple[int, int]:
    """Label incomplete emails, optionally restricted to a review queue.

    Returns:
        (number of emails completed this run, total fully labeled emails)

    Existing valid labels can be retained by pressing Enter at the
    confirmation prompt. Predictions remain suggestions, never ground truth.
    """
    if body_chars < 1:
        raise ValueError("body_chars must be positive")

    if limit is not None and limit < 1:
        raise ValueError("limit must be positive")

    fieldnames, rows = load_rows(path)

    selected_ids = (
        _load_queue_ids(queue_path)
        if queue_path is not None
        else None
    )

    targets = [
        (index, row)
        for index, row in enumerate(rows, start=1)
        if not _fully_labeled(row)
        and (
            selected_ids is None
            or (row.get("email_id") or "").strip() in selected_ids
        )
    ]

    if limit is not None:
        targets = targets[:limit]

    completed_this_run = 0

    for index, row in targets:
        body = (row.get("body_text") or row.get("snippet") or "").strip()

        if len(body) > body_chars:
            body = body[:body_chars].rstrip() + "… [truncated]"

        print("\n" + "=" * 72, file=output)
        print(
            f"Email {index}/{len(rows)} | ID: {row.get('email_id', '')}",
            file=output,
        )
        print(f"Sender: {row.get('sender', '')}", file=output)
        print(f"Subject: {row.get('subject', '')}", file=output)
        print(f"Body: {body or '[No body/snippet available]'}", file=output)

        print(
            "Current labels: "
            + ", ".join(
                f"{field}={row.get(field, '') or 'blank'}"
                for field in LABEL_FIELDS
            ),
            file=output,
        )

        suggestions = [
            f"{field}={row.get(field, '')}"
            for field in (
                "predicted_priority",
                "predicted_needs_response",
                "predicted_category",
            )
            if (row.get(field) or "").strip()
        ]

        if suggestions:
            print(
                "Provisional suggestions (verify; not ground truth): "
                + ", ".join(suggestions),
                file=output,
            )

        print(
            "Priority guide: URGENT=immediate/high-stakes; "
            "NEEDS_REPLY=someone should respond; "
            "INFORMATIONAL=FYI; SPAM=unwanted/malicious.",
            file=output,
        )

        for field, choices in (
            ("true_priority", PRIORITIES),
            ("true_needs_response", NEEDS_RESPONSE),
            ("true_category", CATEGORIES),
        ):
            current = (row.get(field) or "").strip()

            if current in choices:
                keep = input_fn(
                    f"Keep {field}={current}? [Y/n/q]: "
                ).strip().lower()

                if keep == "q":
                    save_rows(path, fieldnames, rows)
                    total = sum(_fully_labeled(item) for item in rows)
                    print("Progress saved; exiting.", file=output)
                    return completed_this_run, total

                if keep != "n":
                    continue

            elif current:
                print(
                    f"Existing value {current!r} is invalid for {field}; "
                    "please correct it.",
                    file=output,
                )

            value = _ask_choice(
                field,
                choices,
                input_fn,
                output,
            )

            if value is None:
                save_rows(path, fieldnames, rows)
                total = sum(_fully_labeled(item) for item in rows)
                print("Progress saved; exiting.", file=output)
                return completed_this_run, total

            row[field] = value

        if _fully_labeled(row):
            completed_this_run += 1

        # Save after each email so interruptions do not lose previous labels.
        save_rows(path, fieldnames, rows)
        print("Saved to CSV.", file=output)

    total_labeled = sum(_fully_labeled(row) for row in rows)

    print(
        f"Finished this pass: {completed_this_run} emails completed; "
        f"{total_labeled}/{len(rows)} fully labeled.",
        file=output,
    )

    return completed_this_run, total_labeled


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument(
        "--input",
        type=Path,
        default=Path("data/email_labels.csv"),
    )
    parser.add_argument("--body-chars", type=int, default=700)
    parser.add_argument(
        "--queue",
        type=Path,
        help="Only label email IDs present in this review-queue CSV",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Maximum number of incomplete emails to process",
    )

    args = parser.parse_args()

    try:
        label_dataset(
            args.input,
            body_chars=args.body_chars,
            queue_path=args.queue,
            limit=args.limit,
        )
    except (FileNotFoundError, ValueError) as exc:
        parser.error(str(exc))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
