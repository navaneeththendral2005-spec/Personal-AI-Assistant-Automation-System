"""Add true_is_spam to a private email-label CSV without discarding legacy labels.

Usage:
    python -m scripts.migrate_email_label_schema --input data/email_labels.csv
    python -m scripts.migrate_email_label_schema --input data/email_labels.csv --no-backup

The migration is additive and idempotent. Existing non-empty true_is_spam values
are preserved. Legacy true_priority values, including SPAM, are never rewritten.
"""
from __future__ import annotations

import argparse
import csv
import os
import shutil
from pathlib import Path

VALID_SPAM = {"yes", "no"}
VALID_PRIORITIES = {"URGENT", "NEEDS_REPLY", "INFORMATIONAL", "SPAM"}


def migrate_csv(path: Path, *, backup: bool = True) -> dict[str, int]:
    """Add/fill ``true_is_spam`` and atomically write the CSV.

    A pre-existing human-entered ``true_is_spam`` value is retained. For rows
    without one, ``true_priority=SPAM`` maps to ``yes`` and another valid
    legacy priority maps to ``no``. Rows without a usable legacy priority stay
    blank rather than inventing a label.
    """
    if not path.is_file():
        raise FileNotFoundError(f"Input CSV not found: {path}")

    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or "email_id" not in reader.fieldnames:
            raise ValueError("CSV must contain an email_id column")
        fieldnames = list(reader.fieldnames)
        rows = [dict(row) for row in reader]

    if "true_priority" not in fieldnames:
        raise ValueError("CSV must contain the existing true_priority column")
    if "true_is_spam" not in fieldnames:
        fieldnames.append("true_is_spam")

    counts = {"rows": len(rows), "derived_yes": 0, "derived_no": 0,
              "preserved_existing": 0, "left_blank": 0}
    for row in rows:
        existing = (row.get("true_is_spam") or "").strip().lower()
        if existing:
            if existing not in VALID_SPAM:
                raise ValueError(
                    f"Invalid true_is_spam={existing!r} for email_id="
                    f"{row.get('email_id', '')!r}; expected yes/no"
                )
            row["true_is_spam"] = existing
            counts["preserved_existing"] += 1
            continue

        priority = (row.get("true_priority") or "").strip().upper()
        if priority == "SPAM":
            row["true_is_spam"] = "yes"
            counts["derived_yes"] += 1
        elif priority in VALID_PRIORITIES:
            row["true_is_spam"] = "no"
            counts["derived_no"] += 1
        else:
            row["true_is_spam"] = ""
            counts["left_blank"] += 1

    if backup:
        backup_path = path.with_suffix(path.suffix + ".bak")
        if backup_path.exists():
            raise FileExistsError(
                f"Backup already exists: {backup_path}. Move it or rerun with --no-backup."
            )
        shutil.copy2(path, backup_path)

    temp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        with temp_path.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()

    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("data/email_labels.csv"))
    parser.add_argument("--no-backup", action="store_true", help="Do not create a .bak copy")
    args = parser.parse_args()
    try:
        counts = migrate_csv(args.input, backup=not args.no_backup)
    except (FileNotFoundError, FileExistsError, ValueError) as exc:
        parser.error(str(exc))
    print(f"Migrated {args.input}: {counts}")
    print("Legacy true_priority values were preserved; inspect blank true_is_spam rows manually.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
