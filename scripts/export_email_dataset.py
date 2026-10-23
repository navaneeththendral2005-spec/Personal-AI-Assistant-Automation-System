"""Export stored FRIDAY emails to a private, resumable labeling CSV.

This script only reads the local database. It never changes Gmail messages.
Existing human labels are preserved when the export is refreshed.
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

COLUMNS = (
    "email_id",
    "sender",
    "subject",
    "snippet",
    "body_text",
    "labels",
    "true_priority",
    "true_needs_response",
    "true_category",
)
LABEL_FIELDS = ("true_priority", "true_needs_response", "true_category")


def read_existing_rows(path: Path) -> dict[str, dict[str, str]]:
    """Read existing rows keyed by email ID so refreshes never discard progress."""
    if not path.exists():
        return {}
    with path.open("r", newline="", encoding="utf-8-sig") as csv_file:
        reader = csv.DictReader(csv_file)
        if not reader.fieldnames or "email_id" not in reader.fieldnames:
            raise ValueError(f"Existing CSV {path} has no email_id column")
        saved: dict[str, dict[str, str]] = {}
        for row in reader:
            email_id = (row.get("email_id") or "").strip()
            if email_id:
                normalized = {column: (row.get(column) or "") for column in COLUMNS}
                saved[email_id] = normalized
        return saved


def export_email_dataset(
    output_path: Path,
    *,
    limit: int = 500,
    include_empty_bodies: bool = False,
    session_factory: Callable[[], Session] | None = None,
) -> int:
    """Export recent stored emails, preserving labels already entered by a human.

    Returns the number of exported rows. Label values are never inferred from
    the production classifier; they remain blank until a person labels them.
    """
    if not 1 <= limit <= 1000:
        raise ValueError("limit must be between 1 and 1000")

    # Import application/database modules lazily so `--help` works without a configured DB.
    from backend.models import EmailMessage

    if session_factory is None:
        from backend.database import SessionLocal

        session_factory = SessionLocal

    existing_rows = read_existing_rows(output_path)
    statement = select(EmailMessage).order_by(
        EmailMessage.message_date.desc().nullslast(), EmailMessage.id.desc()
    )
    if not include_empty_bodies:
        statement = statement.where(
            EmailMessage.body_text.is_not(None), EmailMessage.body_text != ""
        )
    statement = statement.limit(limit)

    db = session_factory()
    try:
        emails = list(db.scalars(statement).all())
    finally:
        db.close()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    refreshed_rows: list[dict[str, str]] = []
    refreshed_ids: set[str] = set()
    for email in emails:
        email_id = email.gmail_message_id
        prior = existing_rows.get(email_id, {})
        refreshed_ids.add(email_id)
        refreshed_rows.append({
            "email_id": email_id,
            "sender": email.sender or "",
            "subject": email.subject or "",
            "snippet": email.snippet or "",
            "body_text": email.body_text or "",
            "labels": "|".join(email.labels or []),
            "true_priority": prior.get("true_priority", ""),
            "true_needs_response": prior.get("true_needs_response", ""),
            "true_category": prior.get("true_category", ""),
        })

    # Keep previously exported rows outside the current DB query window as well.
    # This prevents a later refresh from losing labels for older messages.
    refreshed_rows.extend(
        row for email_id, row in existing_rows.items() if email_id not in refreshed_ids
    )
    with output_path.open("w", newline="", encoding="utf-8-sig") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(refreshed_rows)
    return len(emails)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("data/email_labels.csv"))
    parser.add_argument("--limit", type=int, default=500, help="Maximum emails to export (1–1000)")
    parser.add_argument("--include-empty-bodies", action="store_true")
    args = parser.parse_args()
    try:
        count = export_email_dataset(
            args.output, limit=args.limit, include_empty_bodies=args.include_empty_bodies
        )
    except ValueError as exc:
        parser.error(str(exc))
    print(f"Exported {count} emails to {args.output}")
    print("Existing human labels were preserved; unlabeled fields remain blank.")
    print("This file can contain private email content. Keep it local and uncommitted.")
    if count < 200:
        print(f"NOTE: {count} eligible emails exported; 200+ labeled emails are needed for the planned evaluation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
