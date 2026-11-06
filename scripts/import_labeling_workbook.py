"""Import FRIDAY blind-labeling workbooks into a separate v2 annotation CSV.

All processing is local. Console output contains aggregate counts only; never
prints email IDs, senders, subjects, notes, or message content.

Provenance rules (ground truth stays human)
-------------------------------------------
* ``--labeler`` is required. ``human`` means the workbook was labeled by the
  person; ``ai_draft`` means an AI assistant filled it in.
* AI drafts never enter the human annotation file. They are written to a separate
  quarantine file (default ``data/labeling/drafts/email_labels_ai_drafts.csv``).
* A workbook is treated as an AI draft, whatever ``--labeler`` says, when ANY of its
  notes starts with an AI-draft marker such as ``claude_draft:``. The WHOLE workbook
  is quarantined, because AI-filled rows with empty notes cannot be told apart from
  human rows.
* Every human annotation carries ``labeler=human`` so later analysis can prove who
  produced it.
"""
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
CATEGORIES = {"JOB", "FINANCE", "PERSONAL", "PROMOTIONS", "UPDATES", "OTHER"}
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
    "accepted", "new_records", "duplicates", "conflict", "invalid", "incomplete",
    "existing_records", "total_records", "draft_note_rows", "quarantined_workbooks",
    "quarantined_ai_draft", "draft_new_records", "draft_duplicates", "draft_conflict",
    "draft_total_records",
)

# "claude_draft:", "AI draft:", "ChatGPT-draft :" ... at the start of a note.
AI_DRAFT_NOTE = re.compile(r"^\s*(?:claude|chatgpt|gpt|openai|llm|ai)[\s_\-]*draft\s*:", re.IGNORECASE)


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
        imported_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for email_id, record in seen.items():
            if email_id in conflicts:
                counts["conflict"] += 1
                continue
            accepted.append({
                "email_id": email_id, **record,
                "schema_version": meta["schema version"],
                "guide_version": meta["guide version"],
                "label_round": "blind_relabel" if labeler == "human" else "ai_draft",
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


def import_workbooks(
    workbook_paths: Iterable[Path],
    output_path: Path,
    *,
    overwrite: bool = False,
    labeler: str = "human",
    drafts_output: Path | None = None,
) -> dict[str, int]:
    """Import workbooks. Human annotations go to ``output_path``; AI drafts (declared or
    detected from notes) go to ``drafts_output`` and never into ``output_path``."""
    paths = list(workbook_paths)
    if not paths:
        raise ImportErrorSafe("Provide at least one workbook path.")
    if labeler not in LABELERS:
        raise ImportErrorSafe(f"labeler must be one of {LABELERS}.")
    drafts_path = drafts_output or (output_path.parent / DEFAULT_DRAFTS_RELATIVE)

    totals: Counter[str] = Counter({key: 0 for key in SUMMARY_KEYS})
    human_records: list[dict[str, str]] = []
    draft_records: list[dict[str, str]] = []
    human_workbooks = 0
    for path in paths:
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
    if write_human and output_path.exists() and not overwrite:
        raise ImportErrorSafe("Output already exists. Re-run with --overwrite to merge safely; a backup will be created.")
    if draft_records and drafts_path.exists() and not overwrite:
        raise ImportErrorSafe("Drafts file already exists. Re-run with --overwrite to merge safely; a backup will be created.")

    human_existing = _read_annotation_file(output_path, OUTPUT_FIELDS) if write_human else []
    human_candidates = _merge(_index_existing(human_existing), human_records, totals, "duplicates", "conflict") if write_human else {}
    draft_existing = _read_annotation_file(drafts_path, DRAFT_FIELDS) if draft_records else []
    draft_candidates = _merge(_index_existing(draft_existing), draft_records, totals, "draft_duplicates", "draft_conflict") if draft_records else {}

    # All validation is done; only now touch the disk.
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
    parser.add_argument("workbooks", nargs="+", type=Path, help="Completed blind labeling .xlsx workbook(s)")
    parser.add_argument(
        "--labeler", required=True, choices=LABELERS,
        help="Who filled these workbooks: 'human' (you, blind) or 'ai_draft' (an AI assistant). Required.",
    )
    parser.add_argument("--output", type=Path, default=Path("data/email_labels_v2.csv"))
    parser.add_argument("--drafts-output", type=Path, default=None,
                        help="Quarantine file for AI drafts (default: <output dir>/labeling/drafts/email_labels_ai_drafts.csv)")
    parser.add_argument("--overwrite", action="store_true", help="Allow merging into existing outputs; backs them up first")
    args = parser.parse_args(argv)
    try:
        counts = import_workbooks(
            args.workbooks, args.output, overwrite=args.overwrite,
            labeler=args.labeler, drafts_output=args.drafts_output,
        )
    except ImportErrorSafe as exc:
        print(f"Import stopped: {exc}", file=sys.stderr)
        return 2
    print("Import complete (aggregate counts only):")
    for key in ("accepted", "new_records", "duplicates", "conflict", "invalid", "incomplete",
                "existing_records", "total_records", "quarantined_workbooks", "quarantined_ai_draft",
                "draft_new_records", "draft_total_records"):
        print(f"{key}: {counts.get(key, 0)}")
    if counts.get("quarantined_workbooks"):
        print("NOTE: AI-drafted workbook(s) were quarantined and NOT imported as human labels.")
    print(f"Human output: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
