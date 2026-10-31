"""Add message metadata from the local database to a labeled dataset CSV.

The labeled CSV only carries sender, subject, snippet, body, and Gmail labels, so the
timestamp, thread, and recipient features in ``backend.services.email_features`` stay
inactive. This script joins those fields from the ``emails`` table (read-only) into a
NEW CSV:

    python -m scripts.enrich_dataset_metadata
    python -m scripts.evaluate_feature_pipeline --input data/email_labels_enriched.csv

Added columns (only when missing; existing non-empty values are never overwritten):
    message_date           ISO-8601 UTC timestamp
    thread_id              Gmail thread ID
    thread_message_count   number of messages of that thread stored LOCALLY
    recipient              To header as stored

Safety
------
* The input CSV and the database are never modified.
* An existing output file is never overwritten unless ``--overwrite`` is given.
* Rows are matched on ``email_id`` == ``gmail_message_id``; unmatched rows keep blanks.
* Only counts are printed: no senders, subjects, bodies, or IDs.

Attachment metadata is not stored by the Gmail sync yet. Use
``backend.services.email_features.attachment_metadata_from_payload`` when the sync is
extended to keep it.
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.models import EmailMessage

PROJECT_ROOT = Path.cwd()
DEFAULT_INPUT = PROJECT_ROOT / "data" / "email_labels_evaluation.csv"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "email_labels_enriched.csv"
ADDED_COLUMNS = ("message_date", "thread_id", "thread_message_count", "recipient")


class EnrichmentError(ValueError):
    """A safe-to-display input or output problem."""


def _utc_iso(stamp: datetime | None) -> str:
    """ISO-8601 in UTC. SQLite returns naive datetimes and PostgreSQL aware ones, so
    naive values are treated as UTC (how the sync stores them) for a stable format."""
    if stamp is None:
        return ""
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc).isoformat()


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    if not path.is_file():
        raise EnrichmentError(f"Input dataset not found: {path}")
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            headers = list(reader.fieldnames or [])
            if not headers:
                raise EnrichmentError("Input CSV has no header row.")
            if "email_id" not in headers:
                raise EnrichmentError("Input CSV must contain an email_id column.")
            if len(headers) != len(set(headers)):
                raise EnrichmentError("Input CSV has duplicate column names.")
            rows = [{k: (v or "") for k, v in row.items() if k is not None} for row in reader]
    except (OSError, csv.Error) as exc:
        raise EnrichmentError(f"Could not read input CSV ({type(exc).__name__}).") from exc
    return headers, rows


def enrich_dataset(
    input_path: Path,
    output_path: Path,
    session_factory: Callable[[], Session],
    *,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Write ``output_path`` = input rows + database metadata. Returns counts only."""
    if output_path.resolve() == input_path.resolve():
        raise EnrichmentError("Output must be a different file from the input.")
    if output_path.exists() and not overwrite:
        raise EnrichmentError(
            f"Output already exists: {output_path}. Pass --overwrite to replace it."
        )

    headers, rows = _read_csv(input_path)
    email_ids = sorted({(row.get("email_id") or "").strip() for row in rows} - {""})

    found: dict[str, dict[str, Any]] = {}
    thread_counts: dict[str, int] = {}
    with session_factory() as session:
        # Chunk the IN clause so very large datasets stay within DB parameter limits.
        for start in range(0, len(email_ids), 500):
            chunk = email_ids[start:start + 500]
            for message_id, thread_id, recipient, message_date in session.execute(
                select(
                    EmailMessage.gmail_message_id,
                    EmailMessage.thread_id,
                    EmailMessage.recipient,
                    EmailMessage.message_date,
                ).where(EmailMessage.gmail_message_id.in_(chunk))
            ):
                found[message_id] = {
                    "thread_id": thread_id or "",
                    "recipient": recipient or "",
                    "message_date": message_date,
                }
        thread_ids = sorted({v["thread_id"] for v in found.values() if v["thread_id"]})
        for start in range(0, len(thread_ids), 500):
            chunk = thread_ids[start:start + 500]
            for thread_id, count in session.execute(
                select(EmailMessage.thread_id, func.count(EmailMessage.id))
                .where(EmailMessage.thread_id.in_(chunk))
                .group_by(EmailMessage.thread_id)
            ):
                thread_counts[thread_id] = int(count)

    out_headers = headers + [c for c in ADDED_COLUMNS if c not in headers]
    filled: Counter[str] = Counter()
    matched = 0
    out_rows: list[dict[str, str]] = []
    for row in rows:
        new_row = {column: row.get(column, "") for column in out_headers}
        record = found.get((row.get("email_id") or "").strip())
        if record is not None:
            matched += 1
            values = {
                "message_date": _utc_iso(record["message_date"]),
                "thread_id": record["thread_id"],
                "thread_message_count": (
                    str(thread_counts[record["thread_id"]])
                    if record["thread_id"] in thread_counts else ""
                ),
                "recipient": record["recipient"],
            }
            for column, value in values.items():
                if value and not (new_row.get(column) or "").strip():
                    new_row[column] = value
                    filled[column] += 1
        out_rows.append(new_row)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=out_headers, lineterminator="\n")
            writer.writeheader()
            writer.writerows(out_rows)
        temporary.replace(output_path)
    finally:
        if temporary.exists():
            temporary.unlink()

    return {
        "rows": len(rows),
        "matched_in_database": matched,
        "unmatched": len(rows) - matched,
        "columns_added": [c for c in ADDED_COLUMNS if c not in headers],
        "values_filled": {column: filled.get(column, 0) for column in ADDED_COLUMNS},
        "note": "thread_message_count counts messages stored locally, not the full Gmail thread.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--overwrite", action="store_true", help="Replace an existing output file.")
    args = parser.parse_args(argv)

    from backend.database import SessionLocal  # needs DATABASE_URL (see .env)

    try:
        summary = enrich_dataset(args.input, args.output, SessionLocal, overwrite=args.overwrite)
    except EnrichmentError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    print(f"Wrote {args.output} (input and database were not modified).")
    for key, value in summary.items():
        print(f"- {key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
