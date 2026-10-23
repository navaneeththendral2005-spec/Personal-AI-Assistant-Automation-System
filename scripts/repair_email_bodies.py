"""Repair locally stored email bodies with raw HTML or unusual Unicode spacing.

The script uses Gmail read-only GET operations and updates only FRIDAY's local
``emails.body_text`` column. By default it is a dry run; pass ``--apply`` to
write the repaired text to the configured database.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Allow `python scripts/repair_email_bodies.py` from the project root.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import select

from backend.database import SessionLocal
from backend.models import EmailMessage
from backend.services.gmail_service import _needs_body_repair, get_email_by_id, get_gmail_service

logger = logging.getLogger("friday.repair_email_bodies")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Fetch candidate messages from Gmail and save repaired body text locally.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of candidate database rows to inspect (default: all).",
    )
    args = parser.parse_args()

    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be greater than zero")

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    db = SessionLocal()
    try:
        rows = list(
            db.scalars(
                select(EmailMessage)
                .where(EmailMessage.body_text.is_not(None))
                .order_by(EmailMessage.id.asc())
            ).all()
        )
        candidates = [row for row in rows if _needs_body_repair(row.body_text)]
        if args.limit is not None:
            candidates = candidates[: args.limit]

        print(f"Stored emails inspected: {len(rows)}")
        print(f"Bodies that need HTML/whitespace repair: {len(candidates)}")
        if not candidates:
            print("No body repair candidates found. Nothing to do.")
            return 0

        for row in candidates:
            print(f"  id={row.id} gmail_message_id={row.gmail_message_id} subject={row.subject or '(no subject)'}")

        if not args.apply:
            print("\nDry run only; database was not changed.")
            print("Re-run with --apply to repair these local body_text values.")
            return 0

        service = get_gmail_service()
        updated = 0
        skipped = 0
        failed = 0
        for row in candidates:
            try:
                fresh_email = get_email_by_id(row.gmail_message_id, service=service)
                body_text = fresh_email.get("body_text", "")
                if not body_text or _needs_body_repair(body_text):
                    logger.warning(
                        "Could not produce readable text for local email id=%s subject=%r",
                        row.id,
                        row.subject,
                    )
                    skipped += 1
                    continue
                if body_text != row.body_text:
                    row.body_text = body_text
                    updated += 1
                else:
                    skipped += 1
            except Exception:
                logger.exception(
                    "Failed to repair local email id=%s subject=%r", row.id, row.subject
                )
                failed += 1

        db.commit()
        print(
            f"\nRepair finished: updated={updated}, unchanged_or_unreadable={skipped}, "
            f"failed={failed}. Gmail messages were not modified."
        )
        return 1 if failed else 0
    except Exception:
        db.rollback()
        logger.exception("Email body repair failed; local transaction rolled back")
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
