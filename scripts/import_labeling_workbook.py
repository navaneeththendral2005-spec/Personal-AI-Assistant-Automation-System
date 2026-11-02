"""Import FRIDAY blind-labeling workbooks into a separate v2 annotation CSV.

All processing is local. Console output contains aggregate counts only; never
prints email IDs, senders, subjects, notes, or message content.
"""
from __future__ import annotations

import argparse
import csv
import shutil
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from openpyxl import load_workbook

YES_NO = {"yes", "no"}
PRIORITIES = {"URGENT", "NEEDS_REPLY", "INFORMATIONAL"}
CATEGORIES = {"JOB", "FINANCE", "PERSONAL", "PROMOTIONS", "UPDATES", "OTHER"}
LABEL_FIELDS = ("is_spam", "needs_response", "priority", "category", "unsure", "note")
OUTPUT_FIELDS = (
    "email_id", *LABEL_FIELDS, "schema_version", "guide_version", "label_round",
    "batch_name", "imported_at",
)
EXPECTED_HEADERS = ("row_number", "email_id", "sender", "subject", "snippet", "body_preview", *LABEL_FIELDS)


class ImportErrorSafe(ValueError):
    """An import error safe to show in a console."""


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()


def _progress_metadata(workbook) -> dict[str, str]:
    if "Progress" not in workbook.sheetnames:
        raise ImportErrorSafe("Workbook is missing its Progress sheet.")
    sheet = workbook["Progress"]
    metadata: dict[str, str] = {}
    for row in sheet.iter_rows(min_row=2, max_col=2, values_only=True):
        key = _text(row[0]).lower()
        if key in {"batch name", "schema version", "guide version"}:
            metadata[key] = _text(row[1])
    if not metadata.get("batch name") or not metadata.get("schema version") or not metadata.get("guide version"):
        raise ImportErrorSafe("Workbook is missing required batch/schema/guide provenance.")
    return metadata


def _validate_labels(row: dict[str, str]) -> tuple[bool, str]:
    required = ("is_spam", "needs_response", "priority", "category", "unsure")
    if any(not row[field] for field in required):
        return False, "incomplete"
    if row["is_spam"] not in YES_NO:
        return False, "invalid"
    if row["needs_response"] not in YES_NO:
        return False, "invalid"
    if row["priority"] not in PRIORITIES:
        return False, "invalid"
    if row["category"] not in CATEGORIES:
        return False, "invalid"
    if row["unsure"] not in YES_NO:
        return False, "invalid"
    if row["needs_response"] == "yes" and row["priority"] == "INFORMATIONAL":
        return False, "conflict"
    if row["needs_response"] == "no" and row["priority"] == "NEEDS_REPLY":
        return False, "conflict"
    return True, "valid"


def read_workbook(path: Path) -> tuple[list[dict[str, str]], dict[str, int]]:
    if not path.is_file() or path.suffix.lower() != ".xlsx":
        raise ImportErrorSafe("Each input must be an existing .xlsx workbook.")
    try:
        wb = load_workbook(path, read_only=True, data_only=True)
    except Exception as exc:
        raise ImportErrorSafe(f"Could not open a workbook ({type(exc).__name__}).") from exc
    try:
        if "Labeling" not in wb.sheetnames:
            raise ImportErrorSafe("Workbook is missing its Labeling sheet.")
        meta = _progress_metadata(wb)
        ws = wb["Labeling"]
        headers = tuple(_text(c.value) for c in ws[1])
        if headers != EXPECTED_HEADERS:
            raise ImportErrorSafe("Workbook columns do not match the supported blind-labeling schema.")
        accepted: list[dict[str, str]] = []
        counts = Counter()
        seen: dict[str, dict[str, str]] = {}
        conflicts: set[str] = set()
        for values in ws.iter_rows(min_row=2, values_only=True):
            if not values or all(v is None for v in values):
                continue
            email_id = _text(values[1])
            if not email_id:
                counts["invalid"] += 1
                continue
            record = {field: _text(values[6 + i]).lower() if field in {"is_spam", "needs_response", "unsure"} else _text(values[6 + i])
                      for i, field in enumerate(LABEL_FIELDS)}
            # Preserve the note exactly apart from surrounding whitespace.
            record["note"] = _text(values[11])
            ok, status = _validate_labels(record)
            if not ok:
                counts[status] += 1
                continue
            if email_id in seen:
                comparable = tuple(seen[email_id][f] for f in LABEL_FIELDS[:-1])
                current = tuple(record[f] for f in LABEL_FIELDS[:-1])
                if comparable == current:
                    counts["duplicates"] += 1
                else:
                    conflicts.add(email_id)
                continue
            seen[email_id] = record
        # Do not import any ID that appears more than once with differing labels.
        for email_id, record in seen.items():
            if email_id in conflicts:
                counts["conflict"] += 1
                continue
            accepted.append({
                "email_id": email_id, **record,
                "schema_version": meta["schema version"],
                "guide_version": meta["guide version"],
                "label_round": "blind_relabel",
                "batch_name": meta["batch name"],
                "imported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            })
        counts["accepted"] += len(accepted)
        return accepted, dict(counts)
    finally:
        wb.close()


def _read_existing(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    try:
        with path.open("r", newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            if tuple(reader.fieldnames or ()) != OUTPUT_FIELDS:
                raise ImportErrorSafe("Existing output CSV has an unsupported schema; refusing to merge.")
            return [{field: (row.get(field) or "") for field in OUTPUT_FIELDS} for row in reader]
    except (OSError, csv.Error) as exc:
        raise ImportErrorSafe(f"Could not read existing output ({type(exc).__name__}).") from exc


def _backup(path: Path) -> Path:
    backup_dir = path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = backup_dir / f"{path.stem}.{stamp}{path.suffix}"
    shutil.copy2(path, backup)
    return backup


def _annotation_key(record: dict[str, str]) -> tuple[str, str, str, str, str]:
    """Identify one annotation, not one email.

    The same email may legitimately have multiple independent labels across
    guide versions, labeling rounds, or batches. A repeated annotation is
    identified by the full provenance tuple below.
    """
    return (
        record.get("email_id", ""),
        record.get("schema_version", ""),
        record.get("guide_version", ""),
        record.get("label_round", ""),
        record.get("batch_name", ""),
    )


def import_workbooks(workbook_paths: Iterable[Path], output_path: Path, *, overwrite: bool = False) -> dict[str, int]:
    paths = list(workbook_paths)
    if not paths:
        raise ImportErrorSafe("Provide at least one workbook path.")
    if output_path.exists() and not overwrite:
        raise ImportErrorSafe("Output already exists. Re-run with --overwrite to merge safely; a backup will be created.")

    existing = _read_existing(output_path)
    existing_by_key: dict[tuple[str, str, str, str, str], dict[str, str]] = {}
    for record in existing:
        key = _annotation_key(record)
        if not key[0] or any(not value for value in key[1:]):
            raise ImportErrorSafe("Existing output contains missing annotation identifiers or provenance.")
        if key in existing_by_key:
            raise ImportErrorSafe("Existing output contains duplicate annotation keys.")
        existing_by_key[key] = record

    totals = Counter()
    candidates: dict[tuple[str, str, str, str, str], dict[str, str]] = {}
    conflicting_keys: set[tuple[str, str, str, str, str]] = set()
    for path in paths:
        records, counts = read_workbook(Path(path))
        totals.update(counts)
        for record in records:
            key = _annotation_key(record)
            prior = candidates.get(key) or existing_by_key.get(key)
            if prior is not None:
                if tuple(prior[field] for field in LABEL_FIELDS) == tuple(record[field] for field in LABEL_FIELDS):
                    totals["duplicates"] += 1
                else:
                    conflicting_keys.add(key)
                continue
            candidates[key] = record

    # A disagreement for the same annotation identity is not silently resolved.
    # Keep a pre-existing record intact; exclude conflicting new candidates.
    for key in conflicting_keys:
        candidates.pop(key, None)
    totals["conflict"] += len(conflicting_keys)
    merged = existing + list(candidates.values())

    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        _backup(output_path)
    temp = output_path.with_name(output_path.name + ".tmp")
    try:
        with temp.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS)
            writer.writeheader()
            writer.writerows(merged)
        temp.replace(output_path)
    finally:
        if temp.exists():
            temp.unlink()
    totals["new_records"] = len(candidates)
    totals["existing_records"] = len(existing)
    totals["total_records"] = len(merged)
    return dict(totals)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workbooks", nargs="+", type=Path, help="Completed blind labeling .xlsx workbook(s)")
    parser.add_argument("--output", type=Path, default=Path("data/email_labels_v2.csv"))
    parser.add_argument("--overwrite", action="store_true", help="Allow merging into an existing output; backs it up first")
    args = parser.parse_args(argv)
    try:
        counts = import_workbooks(args.workbooks, args.output, overwrite=args.overwrite)
    except ImportErrorSafe as exc:
        print(f"Import stopped: {exc}", file=sys.stderr)
        return 2
    print("Import complete (aggregate counts only):")
    for key in ("accepted", "new_records", "duplicates", "conflict", "invalid", "incomplete", "existing_records", "total_records"):
        print(f"{key}: {counts.get(key, 0)}")
    print(f"Output: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
