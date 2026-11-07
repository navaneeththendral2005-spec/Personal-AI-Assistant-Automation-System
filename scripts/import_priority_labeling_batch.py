from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from scripts.priority_engine_splits import MANIFEST_VERSION


DEFAULT_WORKBOOK = Path("data/labeling/priority_targeted_batch.xlsx")
DEFAULT_INPUT = Path("data/email_labels_evaluation.csv")
DEFAULT_MANIFEST = Path("data/priority_engine_split.json")
DEFAULT_OUTPUT = Path("data/email_labels_evaluation_priority_batch1.csv")

WORKSHEET_NAME = "Priority Labeling"
GUIDE_SHEET_NAME = "Guide"

EXPECTED_HEADERS = (
    "row_number",
    "email_id",
    "sender",
    "subject",
    "snippet",
    "body_preview",
    "priority",
    "unsure",
    "note",
)

ALLOWED_PRIORITIES = {"URGENT", "NEEDS_REPLY", "INFORMATIONAL"}


class PriorityImportError(ValueError):
    """A validation error that is safe to display in the terminal."""


def _read_source_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    if not path.is_file():
        raise PriorityImportError(f"Input dataset not found: {path}")

    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            headers = list(reader.fieldnames or [])
            rows = list(reader)

    except (OSError, csv.Error) as exc:
        raise PriorityImportError(
            f"Could not read input dataset ({type(exc).__name__})."
        ) from exc

    if not headers or len(headers) != len(set(headers)):
        raise PriorityImportError("Input dataset has missing or duplicate headers.")

    if "email_id" not in headers or "true_priority" not in headers:
        raise PriorityImportError(
            "Input dataset must contain email_id and true_priority columns."
        )

    if any(None in row for row in rows):
        raise PriorityImportError("Input dataset contains rows with extra cells.")

    normalized_rows = [
        {field: (row.get(field) or "") for field in headers}
        for row in rows
    ]
    return headers, normalized_rows


def _index_unique_ids(
    rows: list[dict[str, str]],
    source_name: str,
) -> dict[str, dict[str, str]]:
    indexed: dict[str, dict[str, str]] = {}
    missing = 0
    duplicates = 0

    for row in rows:
        email_id = (row.get("email_id") or "").strip()

        if not email_id:
            missing += 1
        elif email_id in indexed:
            duplicates += 1
        else:
            indexed[email_id] = row

    if missing or duplicates:
        raise PriorityImportError(
            f"{source_name} identity check failed: "
            f"{missing} missing IDs and {duplicates} duplicate IDs."
        )

    return indexed


def _read_frozen_test_ids(path: Path) -> set[str]:
    if not path.is_file():
        raise PriorityImportError(
            "Frozen split manifest is missing; refusing to import."
        )

    try:
        manifest: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PriorityImportError(
            "Frozen split manifest is unreadable."
        ) from exc

    if not isinstance(manifest, dict) or manifest.get("version") != MANIFEST_VERSION:
        raise PriorityImportError(
            "Frozen split manifest has an unsupported version."
        )

    tasks = manifest.get("tasks")
    priority_task = tasks.get("priority") if isinstance(tasks, dict) else None

    if not isinstance(priority_task, dict):
        raise PriorityImportError(
            "Priority test assignment is missing from the frozen manifest."
        )

    test_ids = priority_task.get("test_ids")

    if (
        not isinstance(test_ids, list)
        or any(not isinstance(value, str) or not value.strip() for value in test_ids)
        or len(test_ids) != len(set(test_ids))
    ):
        raise PriorityImportError("Frozen priority test IDs are malformed.")

    return set(test_ids)


def _validate_guide(workbook: Any) -> None:
    """Require the actual frozen v2.1 guide embedded by the batch generator."""
    if GUIDE_SHEET_NAME not in workbook.sheetnames:
        raise PriorityImportError("Workbook is missing its Guide sheet.")

    lines = [
        str(row[0]).strip()
        for row in workbook[GUIDE_SHEET_NAME].iter_rows(values_only=True)
        if row and row[0] is not None
    ]
    normalized = "\\n".join(lines)
    required = (
        "# FRIDAY Email Labeling Guide — v2.1",
        "**Status:** FROZEN",
        "**Schema version:** `v2.1`",
        "**Guide version:** `v2.1`",
    )
    if any(marker not in normalized for marker in required):
        raise PriorityImportError(
            "Workbook Guide sheet is not the canonical frozen v2.1 guide."
        )


def _read_priority_workbook(
    path: Path,
) -> tuple[list[dict[str, str]], dict[str, int]]:
    if not path.is_file() or path.suffix.lower() != ".xlsx":
        raise PriorityImportError("Workbook must be an existing .xlsx file.")

    try:
        workbook = load_workbook(path, read_only=True, data_only=True)
    except Exception as exc:
        raise PriorityImportError(
            f"Could not open workbook ({type(exc).__name__})."
        ) from exc

    try:
        if WORKSHEET_NAME not in workbook.sheetnames:
            raise PriorityImportError(
                f"Workbook is missing its {WORKSHEET_NAME!r} worksheet."
            )

        _validate_guide(workbook)
        worksheet = workbook[WORKSHEET_NAME]

        headers = tuple(
            str(cell.value or "").strip()
            for cell in worksheet[1]
        )
        if headers != EXPECTED_HEADERS:
            raise PriorityImportError(
                "Priority worksheet columns do not match the expected schema."
            )

        # Keep every row for duplicate/holdout checks. Only verified rows
        # (unsure=no) are returned as import candidates.
        by_id: dict[str, tuple[str, str, str]] = {}
        conflicting_ids: set[str] = set()
        counts: Counter[str] = Counter()

        for values in worksheet.iter_rows(min_row=2, values_only=True):
            if not values or all(value is None for value in values):
                continue

            counts["workbook_rows"] += 1

            email_id = str(values[1] or "").strip()
            priority = str(values[6] or "").strip().upper()
            unsure = str(values[7] or "").strip().lower()
            note = str(values[8] or "").strip()

            if (
                not email_id
                or priority not in ALLOWED_PRIORITIES
                or unsure not in {"yes", "no"}
            ):
                counts["invalid_rows"] += 1
                continue

            annotation = (priority, unsure, note)

            if email_id in by_id:
                if by_id[email_id] == annotation:
                    counts["duplicate_rows"] += 1
                else:
                    conflicting_ids.add(email_id)
                continue

            by_id[email_id] = annotation

        if counts["invalid_rows"]:
            raise PriorityImportError(
                f"Workbook contains {counts['invalid_rows']} invalid annotation rows; no import was performed."
            )
        if counts["duplicate_rows"]:
            raise PriorityImportError(
                f"Workbook contains {counts['duplicate_rows']} duplicate IDs; no import was performed."
            )
        if conflicting_ids:
            raise PriorityImportError(
                f"Workbook contains {len(conflicting_ids)} conflicting IDs; no import was performed."
            )

        verified_records: list[dict[str, str]] = []

        for email_id, annotation in by_id.items():
            if email_id in conflicting_ids:
                continue

            priority, unsure, note = annotation
            if unsure == "yes":
                counts["uncertain_excluded"] += 1
                continue

            verified_records.append(
                {
                    "email_id": email_id,
                    "priority": priority,
                    "note": note,
                }
            )

        counts["conflicting_ids"] = len(conflicting_ids)
        counts["verified_labels"] = len(verified_records)

        return verified_records, dict(counts)

    finally:
        workbook.close()


def prepare_priority_import(
    workbook_path: Path,
    input_path: Path,
    manifest_path: Path,
    output_path: Path,
) -> dict[str, int]:
    """Validate inputs and create a new CSV containing verified priority labels."""
    if output_path.exists():
        raise PriorityImportError(
            "Output already exists; choose a new output path. "
            "Existing files are never overwritten."
        )

    headers, source_rows = _read_source_csv(input_path)
    source_by_id = _index_unique_ids(source_rows, "Input dataset")
    frozen_ids = _read_frozen_test_ids(manifest_path)

    verified_records, workbook_counts = _read_priority_workbook(workbook_path)

    # Also check all workbook IDs, including uncertain/invalid annotations,
    # against the frozen holdout. Re-read the sheet in a compact validation
    # pass so no uncertain row can accidentally enter the frozen test set.
    workbook = load_workbook(workbook_path, read_only=True, data_only=True)
    try:
        worksheet = workbook[WORKSHEET_NAME]
        all_workbook_ids: set[str] = set()
        duplicate_workbook_ids: set[str] = set()
        for values in worksheet.iter_rows(min_row=2, values_only=True):
            if not values or all(value is None for value in values):
                continue
            email_id = str(values[1] or "").strip()
            if email_id:
                if email_id in all_workbook_ids:
                    duplicate_workbook_ids.add(email_id)
                all_workbook_ids.add(email_id)
    finally:
        workbook.close()

    if duplicate_workbook_ids:
        raise PriorityImportError(
            "Workbook contains duplicate IDs; no output was written."
        )

    if all_workbook_ids & frozen_ids:
        raise PriorityImportError(
            "Workbook contains frozen priority test IDs; no output was written."
        )

    missing_ids = all_workbook_ids - set(source_by_id)
    if missing_ids:
        raise PriorityImportError(
            "Workbook IDs do not match the input dataset; some are missing; "
            "no output was written."
        )

    updates = {
        record["email_id"]: record["priority"]
        for record in verified_records
    }

    already_matching = 0
    for email_id, priority in updates.items():
        current = (source_by_id[email_id].get("true_priority") or "").strip().upper()

        if current and current != priority:
            raise PriorityImportError(
                "A verified label conflicts with existing ground truth; "
                "no output was written."
            )

        if current == priority:
            already_matching += 1

    output_rows: list[dict[str, str]] = []
    for source_row in source_rows:
        row = dict(source_row)
        email_id = row["email_id"].strip()

        if email_id in updates and not row["true_priority"].strip():
            row["true_priority"] = updates[email_id]

        output_rows.append(row)

    # Create a new output atomically. Exclusive mode prevents clobbering
    # another temporary file; the destination is never overwritten.
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_name(output_path.name + ".tmp")

    try:
        with temp_path.open("x", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=headers,
                extrasaction="raise",
            )
            writer.writeheader()
            writer.writerows(output_rows)

        if output_path.exists():
            raise PriorityImportError(
                "Output appeared during import; refusing to overwrite it."
            )

        temp_path.rename(output_path)
    finally:
        if temp_path.exists():
            temp_path.unlink()

    return {
        "workbook_rows": workbook_counts.get("workbook_rows", 0),
        "verified_labels": len(updates),
        "uncertain_excluded": workbook_counts.get("uncertain_excluded", 0),
        "invalid_rows": workbook_counts.get("invalid_rows", 0),
        "duplicate_rows": workbook_counts.get("duplicate_rows", 0),
        "conflicting_ids": workbook_counts.get("conflicting_ids", 0),
        "already_matching_labels": already_matching,
        "new_labels_applied": len(updates) - already_matching,
        "frozen_test_ids_excluded": 0,
        "dataset_rows": len(output_rows),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workbook", type=Path, default=DEFAULT_WORKBOOK)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)

    try:
        counts = prepare_priority_import(
            workbook_path=args.workbook,
            input_path=args.input,
            manifest_path=args.manifest,
            output_path=args.output,
        )
    except (PriorityImportError, OSError, csv.Error) as exc:
        print(f"Priority import stopped: {exc}", file=sys.stderr)
        return 2

    print("Priority import complete (aggregate counts only):")
    for key, value in counts.items():
        print(f"{key}: {value}")

    print(f"Output: {args.output}")
    print("Source dataset, workbook, and frozen manifest were not modified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
