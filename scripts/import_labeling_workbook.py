from __future__ import annotations

import argparse

import csv

import re

import shutil

import sys

from collections import Counter

from datetime import datetime, timezone

from pathlib import Path

from typing import Iterable

from openpyxl import load_workbook

YES_NO = {"yes", "no"}

PRIORITIES = {"URGENT", "NEEDS_REPLY", "INFORMATIONAL"}

CATEGORIES_V21 = {"JOB", "FINANCE", "PROMOTIONS", "UPDATES", "OTHER"}

LEGACY_CATEGORIES = CATEGORIES_V21 | {"PERSONAL"}

LABEL_FIELDS = ("is_spam", "needs_response", "priority", "category", "unsure", "note")

LABELERS = ("human", "ai_draft")

OUTPUT_FIELDS = (

    "email_id", *LABEL_FIELDS, "schema_version", "guide_version", "label_round",

    "batch_name", "imported_at", "labeler",

)

# Files written before provenance tracking had no ``labeler`` column.

LEGACY_OUTPUT_FIELDS = tuple(field for field in OUTPUT_FIELDS if field != "labeler")

DRAFT_FIELDS = (*OUTPUT_FIELDS, "draft_reason")

EXPECTED_HEADERS = ("row_number", "email_id", "sender", "subject", "snippet", "body_preview", *LABEL_FIELDS)

DEFAULT_DRAFTS_RELATIVE = Path("labeling") / "drafts" / "email_labels_ai_drafts.csv"

# Every import summary contains exactly these keys (zero when not applicable).

SUMMARY_KEYS = (

    "accepted", "new_records", "duplicates", "conflict", "invalid", "incomplete", "uncertain",

    "existing_records", "total_records", "draft_note_rows", "quarantined_workbooks",

    "quarantined_ai_draft", "draft_new_records", "draft_duplicates", "draft_conflict",

    "draft_total_records",

)

# "claude_draft:", "AI draft:", "ChatGPT-draft :" ... at the start of a note.

AI_DRAFT_NOTE = re.compile(r"^\s*(?:claude|chatgpt|gpt|openai|llm|ai)[\s_-]*draft\s*:", re.IGNORECASE)

class ImportErrorSafe(ValueError):

    """An import error safe to show in a console."""

def is_ai_draft_note(note: object) -> bool:

    """True when a note carries an AI-draft provenance marker."""

    return bool(AI_DRAFT_NOTE.match("" if note is None else str(note)))

def _text(value: object) -> str:

    return "" if value is None else str(value).strip()

def _progress_metadata(workbook) -> dict[str, str]:

    if "Progress" not in workbook.sheetnames:

        raise ImportErrorSafe("Workbook is missing its Progress sheet.")

    sheet = workbook["Progress"]

    metadata: dict[str, str] = {}

    for row in sheet.iter_rows(min_row=2, max_col=2, values_only=True):

        key = _text(row[0]).lower()

        if key in {"batch name", "schema version", "guide version", "mode"}:

            metadata[key] = _text(row[1])

    if not metadata.get("batch name") or not metadata.get("schema version") or not metadata.get("guide version"):

        raise ImportErrorSafe("Workbook is missing required batch/schema/guide provenance.")

    return metadata

def _validate_labels(row: dict[str, str], guide_version: str = "v2.1") -> tuple[bool, str]:

    required = ("is_spam", "needs_response", "priority", "category", "unsure")

    if any(not row[field] for field in required):

        return False, "incomplete"

    if row["is_spam"] not in YES_NO:

        return False, "invalid"

    if row["needs_response"] not in YES_NO:

        return False, "invalid"

    if row["priority"] not in PRIORITIES:

        return False, "invalid"

    allowed_categories = LEGACY_CATEGORIES if guide_version == "v2.0-draft" else CATEGORIES_V21

    if row["category"] not in allowed_categories:

        return False, "invalid"

    if row["unsure"] not in YES_NO:

        return False, "invalid"

    # Uncertain annotations remain in the source workbook for review, but are excluded from verified imports.

    if row["unsure"] == "yes":

        return False, "uncertain"

    if row["needs_response"] == "yes" and row["priority"] == "INFORMATIONAL":

        return False, "conflict"

    if row["needs_response"] == "no" and row["priority"] == "NEEDS_REPLY":

        return False, "conflict"

    return True, "valid"

def read_workbook(path: Path, labeler: str = "human") -> tuple[list[dict[str, str]], dict[str, int]]:

    """Read one workbook. ``counts['draft_note_rows']`` counts EVERY row (valid or not)

    whose note carries an AI-draft marker."""

    if labeler not in LABELERS:

        raise ImportErrorSafe(f"labeler must be one of {LABELERS}.")

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

        counts: Counter[str] = Counter()

        seen: dict[str, dict[str, str]] = {}

        conflicts: set[str] = set()

        for values in ws.iter_rows(min_row=2, values_only=True):

            if not values or all(v is None for v in values):

                continue

            if is_ai_draft_note(values[11]):

                counts["draft_note_rows"] += 1

            email_id = _text(values[1])

            if not email_id:

                counts["invalid"] += 1

                continue

            record = {field: _text(values[6 + i]).lower() if field in {"is_spam", "needs_response", "unsure"} else _text(values[6 + i])

                      for i, field in enumerate(LABEL_FIELDS)}

            # Preserve the note exactly apart from surrounding whitespace.

            record["note"] = _text(values[11])

            ok, status = _validate_labels(record, meta["guide version"])

            if not ok:

                counts[status] += 1

                continue

            if email_id in seen:

                comparable = tuple(seen[email_id][f] for f in LABEL_FIELDS[:-1])

                current = tuple(record[f] for f in LABEL_FIELDS[:-1])

                if comparable == current and seen[email_id]["note"] == record["note"]:

                    counts["duplicates"] += 1

                else:

                    conflicts.add(email_id)

                continue

            seen[email_id] = record

        # Do not import any ID that appears more than once with differing labels.

        imported_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

        for email_id, record in seen.items():

            if email_id in conflicts:

                counts["conflict"] += 1

                continue

            accepted.append({

                "email_id": email_id, **record,

                "schema_version": meta["schema version"],

                "guide_version": meta["guide version"],

                "label_round": (
                    "ai_draft" if labeler == "ai_draft"
                    else "frozen_test" if meta.get("mode", "").lower() == "test_v2"
                    else "minority" if meta.get("mode", "").lower() == "minority"
                    else "blind_relabel"
                ),

                "batch_name": meta["batch name"],

                "imported_at": imported_at,

                "labeler": labeler,

            })

        counts["accepted"] += len(accepted)

        return accepted, dict(counts)

    finally:

        wb.close()

def _read_annotation_file(path: Path, fields: tuple[str, ...]) -> list[dict[str, str]]:

    if not path.exists():

        return []

    try:

        with path.open("r", newline="", encoding="utf-8-sig") as handle:

            reader = csv.DictReader(handle)

            header = tuple(reader.fieldnames or ())

            if header == LEGACY_OUTPUT_FIELDS and fields == OUTPUT_FIELDS:

                raise ImportErrorSafe(

                    "Existing output predates provenance tracking (no 'labeler' column). "

                    "Run `python -m scripts.quarantine_ai_drafts` first to declare which "

                    "batches are human and which are AI drafts."

                )

            if header != fields:

                raise ImportErrorSafe("Existing output CSV has an unsupported schema; refusing to merge.")

            return [{field: (row.get(field) or "") for field in fields} for row in reader]

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

def _index_existing(existing: list[dict[str, str]]) -> dict[tuple[str, str, str, str, str], dict[str, str]]:

    by_key: dict[tuple[str, str, str, str, str], dict[str, str]] = {}

    for record in existing:

        key = _annotation_key(record)

        if not key[0] or any(not value for value in key[1:]):

            raise ImportErrorSafe("Existing output contains missing annotation identifiers or provenance.")

        if key in by_key:

            raise ImportErrorSafe("Existing output contains duplicate annotation keys.")

        by_key[key] = record

    return by_key

def _merge(

    existing_by_key: dict[tuple[str, str, str, str, str], dict[str, str]],

    records: list[dict[str, str]],

    totals: Counter,

    duplicate_key: str,

    conflict_key: str,

) -> dict[tuple[str, str, str, str, str], dict[str, str]]:

    """Return the new, non-duplicate, non-conflicting records keyed by annotation."""

    candidates: dict[tuple[str, str, str, str, str], dict[str, str]] = {}

    conflicting_keys: set[tuple[str, str, str, str, str]] = set()

    for record in records:

        key = _annotation_key(record)

        prior = candidates.get(key) or existing_by_key.get(key)

        if prior is not None:

            if tuple(prior[field] for field in LABEL_FIELDS) == tuple(record[field] for field in LABEL_FIELDS):

                totals[duplicate_key] += 1

            else:

                conflicting_keys.add(key)

            continue

        candidates[key] = record

    # A disagreement for the same annotation identity is not silently resolved.

    # Keep a pre-existing record intact; exclude conflicting new candidates.

    for key in conflicting_keys:

        candidates.pop(key, None)

    totals[conflict_key] += len(conflicting_keys)

    return candidates

def _write_csv(path: Path, fields: tuple[str, ...], rows: list[dict[str, str]]) -> None:

    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():

        _backup(path)

    temp = path.with_name(path.name + ".tmp")

    try:

        with temp.open("w", newline="", encoding="utf-8-sig") as handle:

            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")

            writer.writeheader()

            writer.writerows(rows)

        temp.replace(path)

    finally:

        if temp.exists():

            temp.unlink()


def _load_task_safety_context(
    dataset_path: Path,
    manifest_path: Path,
    task: str,
) -> tuple[dict[str, dict[str, str]], set[str], str]:
    """Load source IDs and an EXISTING frozen holdout; never creates assignments."""
    import json

    if task not in {"category", "priority"}:
        raise ImportErrorSafe("Safe import task must be 'category' or 'priority'.")
    truth_column = "true_category" if task == "category" else "true_priority"
    if not dataset_path.is_file():
        raise ImportErrorSafe(f"Source evaluation dataset not found: {dataset_path}")
    try:
        with dataset_path.open("r", newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            headers = tuple(reader.fieldnames or ())
            rows = list(reader)
    except (OSError, csv.Error) as exc:
        raise ImportErrorSafe(f"Could not read source evaluation dataset ({type(exc).__name__}).") from exc
    if len(headers) != len(set(headers)) or not {"email_id", truth_column}.issubset(headers):
        raise ImportErrorSafe(f"Source dataset must have unique headers including email_id and {truth_column}.")
    source: dict[str, dict[str, str]] = {}
    for row in rows:
        email_id = _text(row.get("email_id"))
        if not email_id:
            raise ImportErrorSafe("Source dataset contains a row with a missing email ID.")
        if email_id in source:
            raise ImportErrorSafe("Source dataset contains duplicate email IDs.")
        source[email_id] = row

    if not manifest_path.is_file():
        raise ImportErrorSafe("Frozen split manifest is missing; refusing safe import.")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ImportErrorSafe("Frozen split manifest is unreadable.") from exc
    if not isinstance(manifest, dict) or manifest.get("version") != 1:
        raise ImportErrorSafe("Frozen split manifest has an unsupported version.")
    tasks = manifest.get("tasks")
    entry = tasks.get(task) if isinstance(tasks, dict) else None
    test_ids = entry.get("test_ids") if isinstance(entry, dict) else None
    if (
        not isinstance(test_ids, list)
        or any(not isinstance(value, str) or not value.strip() for value in test_ids)
        or len(test_ids) != len(set(test_ids))
    ):
        raise ImportErrorSafe(f"Frozen {task} test assignment is missing or malformed.")
    return source, set(test_ids), truth_column


def _validate_workbook_task_safety(
    workbook_path: Path,
    source: dict[str, dict[str, str]],
    frozen_ids: set[str],
    truth_column: str,
    task: str,
) -> None:
    """Validate provenance and ALL workbook IDs before any output write."""
    wb = load_workbook(workbook_path, read_only=True, data_only=True)
    try:
        metadata = _progress_metadata(wb)
        if metadata.get("schema version") != "v2.1":
            raise ImportErrorSafe("Safe task imports require schema version v2.1.")
        if metadata.get("guide version") != "v2.1":
            raise ImportErrorSafe("Safe task imports require guide version v2.1.")
        if metadata.get("mode", "").strip().lower() != "minority":
            raise ImportErrorSafe("Safe task imports require Progress mode 'minority'.")
        if "Labeling" not in wb.sheetnames:
            raise ImportErrorSafe("Workbook is missing its Labeling sheet.")
        ws = wb["Labeling"]
        ids: set[str] = set()
        for values in ws.iter_rows(min_row=2, values_only=True):
            if not values or all(value is None for value in values):
                continue
            email_id = _text(values[1] if len(values) > 1 else "")
            if not email_id:
                raise ImportErrorSafe("Workbook contains a row with a missing email ID.")
            if email_id in ids:
                # read_workbook handles duplicate/conflicting annotations; fail closed here.
                raise ImportErrorSafe("Workbook contains duplicate email IDs; resolve them before importing.")
            ids.add(email_id)
            if email_id not in source:
                raise ImportErrorSafe("Workbook contains email IDs absent from the source evaluation dataset.")
            if email_id in frozen_ids:
                raise ImportErrorSafe(f"Workbook overlaps the frozen {task} test set; no import was performed.")
        # Compare target truth only for certain, valid imported records.
        records, _ = read_workbook(workbook_path, labeler="human")
        for record in records:
            existing_truth = _text(source[record["email_id"]].get(truth_column)).upper()
            proposed = record[task].upper()
            if existing_truth and existing_truth != proposed:
                raise ImportErrorSafe(
                    f"A verified {task} annotation conflicts with existing ground truth; no import was performed."
                )
    finally:
        wb.close()


def import_workbooks(

    workbook_paths: Iterable[Path],

    output_path: Path,

    *,

    overwrite: bool = False,

    labeler: str = "human",

    drafts_output: Path | None = None,
    task: str | None = None,
    dataset_path: Path | None = None,
    manifest_path: Path | None = None,
    dry_run: bool = False,

) -> dict[str, int]:

    """Import workbooks. Human annotations go to ``output_path``; AI drafts (declared or

    detected from notes) go to ``drafts_output`` and never into ``output_path``."""

    paths = list(workbook_paths)

    if not paths:

        raise ImportErrorSafe("Provide at least one workbook path.")

    if labeler not in LABELERS:

        raise ImportErrorSafe(f"labeler must be one of {LABELERS}.")

    safety_enabled = any(value is not None for value in (task, dataset_path, manifest_path))
    safety_context = None
    if safety_enabled:
        if task is None or dataset_path is None or manifest_path is None:
            raise ImportErrorSafe("--task, --dataset, and --manifest must be supplied together.")
        safety_context = _load_task_safety_context(dataset_path, manifest_path, task)

    drafts_path = drafts_output or (output_path.parent / DEFAULT_DRAFTS_RELATIVE)

    totals: Counter[str] = Counter({key: 0 for key in SUMMARY_KEYS})

    human_records: list[dict[str, str]] = []

    draft_records: list[dict[str, str]] = []

    human_workbooks = 0

    for path in paths:

        if safety_context is not None:
            source_rows, frozen_ids, truth_column = safety_context
            _validate_workbook_task_safety(Path(path), source_rows, frozen_ids, truth_column, task)

        records, counts = read_workbook(Path(path), labeler)

        quarantine = labeler == "ai_draft" or counts.get("draft_note_rows", 0) > 0

        totals["draft_note_rows"] += counts.get("draft_note_rows", 0)

        if quarantine:

            totals["quarantined_workbooks"] += 1

            reason = "declared_ai_draft" if labeler == "ai_draft" else "note_prefix_whole_workbook"

            for key, value in counts.items():

                if key not in {"accepted", "draft_note_rows"}:

                    totals[key] += value

            for record in records:

                draft_records.append({

                    **record, "labeler": "ai_draft", "label_round": "ai_draft", "draft_reason": reason,

                })

            totals["quarantined_ai_draft"] += len(records)

        else:

            human_workbooks += 1

            for key, value in counts.items():

                if key != "draft_note_rows":

                    totals[key] += value

            human_records.extend(records)

    write_human = human_workbooks > 0

    if not dry_run:
        if write_human and output_path.exists() and not overwrite:
            raise ImportErrorSafe("Output already exists. Re-run with --overwrite to merge safely; a backup will be created.")

        if draft_records and drafts_path.exists() and not overwrite:
            raise ImportErrorSafe("Drafts file already exists. Re-run with --overwrite to merge safely; a backup will be created.")

    human_existing = _read_annotation_file(output_path, OUTPUT_FIELDS) if write_human else []

    human_candidates = _merge(_index_existing(human_existing), human_records, totals, "duplicates", "conflict") if write_human else {}

    draft_existing = _read_annotation_file(drafts_path, DRAFT_FIELDS) if draft_records else []

    draft_candidates = _merge(_index_existing(draft_existing), draft_records, totals, "draft_duplicates", "draft_conflict") if draft_records else {}

    # Dry-run performs the complete validation and merge calculation but never writes
    # files, creates directories, or makes backups.
    if not dry_run:
        if write_human:
            _write_csv(output_path, OUTPUT_FIELDS, human_existing + list(human_candidates.values()))

        if draft_records:
            _write_csv(drafts_path, DRAFT_FIELDS, draft_existing + list(draft_candidates.values()))

    totals["new_records"] = len(human_candidates)

    totals["existing_records"] = len(human_existing)

    totals["total_records"] = len(human_existing) + len(human_candidates)

    totals["draft_new_records"] = len(draft_candidates)

    totals["draft_total_records"] = len(draft_existing) + len(draft_candidates)

    return dict(totals)

def main(argv: list[str] | None = None) -> int:

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)

    parser.add_argument("workbooks", nargs="+", type=Path, help="Completed human/AI labeling .xlsx workbook(s)")

    parser.add_argument(

        "--labeler", required=True, choices=LABELERS,

        help="Who filled these workbooks: 'human' (you, blind) or 'ai_draft' (an AI assistant). Required.",

    )

    parser.add_argument("--output", type=Path, default=Path("data/email_labels_v2.csv"))
    parser.add_argument("--task", choices=("category", "priority"),
                        help="Target task; enables source-dataset and frozen-holdout validation.")
    parser.add_argument("--dataset", type=Path, default=None,
                        help="Source evaluation CSV (required with --task).")
    parser.add_argument("--manifest", type=Path, default=None,
                        help="Existing frozen split manifest (required with --task).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Validate and calculate import counts without writing or backing up any files.")

    parser.add_argument("--drafts-output", type=Path, default=None,

                        help="Quarantine file for AI drafts (default: <output dir>/labeling/drafts/email_labels_ai_drafts.csv)")

    parser.add_argument("--overwrite", action="store_true", help="Allow merging into existing outputs; backs them up first")

    args = parser.parse_args(argv)

    try:

        counts = import_workbooks(

            args.workbooks, args.output, overwrite=args.overwrite,

            labeler=args.labeler, drafts_output=args.drafts_output,
            task=args.task, dataset_path=args.dataset, manifest_path=args.manifest,
            dry_run=args.dry_run,

        )

    except ImportErrorSafe as exc:

        print(f"Import stopped: {exc}", file=sys.stderr)

        return 2

    print(("Dry-run complete (no files were changed; aggregate counts only):"
           if args.dry_run else "Import complete (aggregate counts only):"))

    for key in ("accepted", "new_records", "duplicates", "conflict", "invalid", "incomplete", "uncertain",

                "existing_records", "total_records", "quarantined_workbooks", "quarantined_ai_draft",

                "draft_new_records", "draft_total_records"):

        print(f"{key}: {counts.get(key, 0)}")

    if counts.get("quarantined_workbooks"):

        print("NOTE: AI-drafted workbook(s) were quarantined and NOT imported as human labels.")

    if not args.dry_run:
        print(f"Human output: {args.output}")

    return 0

if __name__ == "__main__":

    raise SystemExit(main())
