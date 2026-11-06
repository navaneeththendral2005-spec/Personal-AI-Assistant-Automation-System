"""Separate AI-drafted annotations from human annotations in an existing v2 label file.

Why this exists
---------------
Earlier imports recorded every workbook as a human "blind_relabel", including workbooks
that an AI assistant had filled in as drafts. Those rows must not count as ground truth.
This tool repairs an existing ``data/email_labels_v2.csv`` by forcing an explicit
decision for every batch.

Usage (PowerShell, from the project root)::

    # 1. See what is in the file (aggregate counts only)
    python -m scripts.quarantine_ai_drafts --list

    # 2. Declare every batch: AI drafts and the batches YOU labeled yourself
    python -m scripts.quarantine_ai_drafts `
        --draft-batch relabel_pilot_20 --draft-batch relabel_batch_21_60 `
        --draft-batch relabel_batch_61_81 --in-place

    # (batches you personally labeled are declared with --confirm-human-batch NAME)

Rules
-----
* A batch is an AI draft when you declare it (``--draft-batch``), when its guide version
  is declared a draft (``--draft-guide-version``), when ANY of its notes starts with an
  AI-draft marker such as ``claude_draft:`` (the WHOLE batch is then quarantined), or
  when a row already says ``labeler=ai_draft``.
* Every other batch must be declared with ``--confirm-human-batch``. Without that the
  tool refuses, so provenance is never assumed.
* The input file is never modified unless ``--in-place`` is given, and then a backup is
  written to ``<dir>/backups/`` first. Output files are never overwritten without
  ``--overwrite``.
* Console output is aggregate counts and batch names only: never email IDs, notes, or
  message content.
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Sequence

from scripts.import_labeling_workbook import (
    DRAFT_FIELDS,
    LEGACY_OUTPUT_FIELDS,
    OUTPUT_FIELDS,
    _annotation_key,
    _write_csv,
    is_ai_draft_note,
)

DEFAULT_INPUT = Path("data/email_labels_v2.csv")
DEFAULT_DRAFTS = Path("data/labeling/drafts/email_labels_ai_drafts.csv")


class QuarantineError(ValueError):
    """A safe-to-display input or decision error."""


def _read_input(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise QuarantineError(f"Input file not found: {path}")
    try:
        with path.open("r", newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            header = tuple(reader.fieldnames or ())
            if header not in {OUTPUT_FIELDS, LEGACY_OUTPUT_FIELDS}:
                raise QuarantineError("Input has an unsupported schema; expected the importer's v2 annotation columns.")
            return [{key: (row.get(key) or "") for key in header} for row in reader]
    except (OSError, csv.Error) as exc:
        raise QuarantineError(f"Could not read the input file ({type(exc).__name__}).") from exc


def _read_drafts(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    try:
        with path.open("r", newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            if tuple(reader.fieldnames or ()) != DRAFT_FIELDS:
                raise QuarantineError("Existing drafts file has an unsupported schema; refusing to merge.")
            return [{key: (row.get(key) or "") for key in DRAFT_FIELDS} for row in reader]
    except (OSError, csv.Error) as exc:
        raise QuarantineError(f"Could not read the drafts file ({type(exc).__name__}).") from exc


def describe_batches(rows: Sequence[dict[str, str]]) -> list[dict[str, Any]]:
    """Aggregate overview, one entry per (batch, guide version)."""
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = (row.get("batch_name", ""), row.get("guide_version", ""))
        entry = groups.setdefault(key, {
            "batch_name": key[0], "guide_version": key[1], "rows": 0,
            "rows_with_ai_draft_note": 0, "labelers": Counter(),
        })
        entry["rows"] += 1
        entry["rows_with_ai_draft_note"] += int(is_ai_draft_note(row.get("note")))
        entry["labelers"][(row.get("labeler") or "(not recorded)")] += 1
    return [
        {**entry, "labelers": dict(sorted(entry["labelers"].items()))}
        for _, entry in sorted(groups.items())
    ]


def classify_rows(
    rows: Sequence[dict[str, str]],
    *,
    draft_batches: Sequence[str] = (),
    draft_guide_versions: Sequence[str] = (),
    confirmed_human_batches: Sequence[str] = (),
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Split rows into (human, drafts). Raises if any batch has no explicit decision."""
    declared_drafts = set(draft_batches)
    draft_versions = set(draft_guide_versions)
    confirmed = set(confirmed_human_batches)
    both = declared_drafts & confirmed
    if both:
        raise QuarantineError(f"A batch cannot be both a draft and human: {sorted(both)}")

    by_batch: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        by_batch[row.get("batch_name", "")].append(row)

    batch_reason: dict[str, str] = {}
    undecided: list[str] = []
    for batch, batch_rows in by_batch.items():
        guide_versions = {r.get("guide_version", "") for r in batch_rows}
        if batch in declared_drafts:
            batch_reason[batch] = "declared_batch"
        elif guide_versions & draft_versions:
            batch_reason[batch] = "declared_guide_version"
        elif any(is_ai_draft_note(r.get("note")) for r in batch_rows):
            batch_reason[batch] = "note_prefix"
        elif any((r.get("labeler") or "").casefold() == "ai_draft" for r in batch_rows):
            batch_reason[batch] = "previously_marked"
        elif batch not in confirmed:
            undecided.append(batch)
    if undecided:
        raise QuarantineError(
            "Provenance is not declared for these batches: "
            f"{sorted(undecided)}. Pass --draft-batch NAME if an AI filled it, or "
            "--confirm-human-batch NAME if you labeled it yourself."
        )

    human: list[dict[str, str]] = []
    drafts: list[dict[str, str]] = []
    for row in rows:
        batch = row.get("batch_name", "")
        reason = batch_reason.get(batch)
        if reason is None and (row.get("labeler") or "").casefold() == "ai_draft":
            reason = "previously_marked"
        base = {field: row.get(field, "") for field in OUTPUT_FIELDS}
        if reason is not None:
            drafts.append({**base, "labeler": "ai_draft", "label_round": "ai_draft", "draft_reason": reason})
        else:
            human.append({**base, "labeler": "human"})
    return human, drafts


def quarantine(
    input_path: Path,
    *,
    draft_batches: Sequence[str] = (),
    draft_guide_versions: Sequence[str] = (),
    confirmed_human_batches: Sequence[str] = (),
    human_output: Path | None = None,
    drafts_output: Path = DEFAULT_DRAFTS,
    in_place: bool = False,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Write cleaned human and quarantined draft files. Returns aggregate counts."""
    rows = _read_input(input_path)
    if not rows:
        raise QuarantineError("The input file has no annotation rows.")
    human, drafts = classify_rows(
        rows, draft_batches=draft_batches, draft_guide_versions=draft_guide_versions,
        confirmed_human_batches=confirmed_human_batches,
    )

    if in_place:
        if human_output is not None and human_output.resolve() != input_path.resolve():
            raise QuarantineError("--in-place cannot be combined with a different --human-output.")
        target = input_path
    else:
        target = human_output or input_path.with_name(input_path.stem + "_human" + input_path.suffix)
        if target.resolve() == input_path.resolve():
            raise QuarantineError("The human output must be a different file unless --in-place is used.")
        if target.exists() and not overwrite:
            raise QuarantineError(f"Output already exists: {target}. Pass --overwrite to replace it (a backup is made).")

    existing_drafts = _read_drafts(drafts_output)
    if drafts and drafts_output.exists() and not overwrite:
        raise QuarantineError(f"Drafts file already exists: {drafts_output}. Pass --overwrite to merge into it (a backup is made).")
    known = {_annotation_key(r) for r in existing_drafts}
    new_drafts = [r for r in drafts if _annotation_key(r) not in known]

    # Validation finished; only now touch the disk. _write_csv backs up an existing
    # target first, which is exactly the input file in --in-place mode.
    _write_csv(target, OUTPUT_FIELDS, human)
    if drafts:
        _write_csv(drafts_output, DRAFT_FIELDS, existing_drafts + new_drafts)

    warnings: list[str] = []
    if not human:
        warnings.append(
            "No human annotations remain in the cleaned file. Label the blind v2.1 pilot yourself "
            "and import it with --labeler human before running the agreement gate."
        )
    return {
        "warnings": warnings,
        "input_rows": len(rows),
        "human_rows": len(human),
        "ai_draft_rows": len(drafts),
        "ai_draft_rows_new_in_drafts_file": len(new_drafts),
        "ai_draft_reasons": dict(sorted(Counter(r["draft_reason"] for r in drafts).items())),
        "human_batches": sorted({r["batch_name"] for r in human}),
        "draft_batches": sorted({r["batch_name"] for r in drafts}),
        "human_output": str(target),
        "drafts_output": str(drafts_output) if drafts else None,
        "input_modified": bool(in_place),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--list", action="store_true", help="Show an aggregate overview of the batches and exit.")
    parser.add_argument("--draft-batch", action="append", default=[], help="Batch an AI filled in (repeatable).")
    parser.add_argument("--draft-guide-version", action="append", default=[],
                        help="Treat every annotation under this guide version as a draft (repeatable).")
    parser.add_argument("--confirm-human-batch", action="append", default=[],
                        help="Batch YOU labeled yourself, blind (repeatable).")
    parser.add_argument("--human-output", type=Path, default=None)
    parser.add_argument("--drafts-output", type=Path, default=DEFAULT_DRAFTS)
    parser.add_argument("--in-place", action="store_true", help="Replace the input with the cleaned human file (backup first).")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)

    try:
        if args.list:
            for entry in describe_batches(_read_input(args.input)):
                print(
                    f"batch={entry['batch_name']} guide={entry['guide_version']} rows={entry['rows']} "
                    f"rows_with_ai_draft_note={entry['rows_with_ai_draft_note']} labelers={entry['labelers']}"
                )
            return 0
        summary = quarantine(
            args.input, draft_batches=args.draft_batch, draft_guide_versions=args.draft_guide_version,
            confirmed_human_batches=args.confirm_human_batch, human_output=args.human_output,
            drafts_output=args.drafts_output, in_place=args.in_place, overwrite=args.overwrite,
        )
    except QuarantineError as exc:
        print(f"Quarantine stopped: {exc}", file=sys.stderr)
        return 2
    print("Quarantine complete (aggregate counts only):")
    for key, value in summary.items():
        if key != "warnings":
            print(f"{key}: {value}")
    for warning in summary["warnings"]:
        print(f"WARNING: {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
