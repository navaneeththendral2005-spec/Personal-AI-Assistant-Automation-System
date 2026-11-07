"""Create a priority-only blind workbook from unlabeled priority candidates.

Run from the repository root. Candidate ranking is advisory only; it never writes
truth labels. All task-specific frozen-test IDs are excluded. Existing labels for
other tasks are preserved in the source and do not disqualify a row.
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import re
import shutil
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Alignment, Font, PatternFill, Protection
from openpyxl.worksheet.datavalidation import DataValidation

from backend.services.email_features import extract_features, parse_sender
from scripts.sample_test_v2 import read_frozen_test_ids

ROOT = Path.cwd()
DEFAULT_INPUT = ROOT / "data/email_labels_evaluation.csv"
DEFAULT_SPLIT = ROOT / "data/priority_engine_split.json"
DEFAULT_OUTPUT = ROOT / "data/labeling/priority_targeted_batch.xlsx"
GUIDE = ROOT / "docs/EMAIL_LABELING_GUIDE.md"
FIELDS = ("row_number", "email_id", "sender", "subject", "snippet", "body_preview", "priority", "unsure", "note")

class BatchError(ValueError):
    pass

def read_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise BatchError("Input dataset was not found.")
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or "email_id" not in reader.fieldnames or "true_priority" not in reader.fieldnames:
            raise BatchError("Input must contain email_id and true_priority columns.")
        return [{k: (v or "") for k, v in row.items() if k is not None} for row in reader]

def score_priority(label: str, features: dict[str, float]) -> float:
    bulk = features["sender_is_noreply"] + features["sender_bulk_local"] + features["sender_bulk_domain"] + features["body_has_unsubscribe"]
    if label == "URGENT":
        return 2 * features["hits_urgency"] + 2 * features["hits_deadline"] + features["hits_security"] + 0.5 * features["subject_upper_ratio"] * 5
    if label == "NEEDS_REPLY":
        human = (1 - min(bulk, 1)) * (1 + features["sender_is_freemail"] + features["body_personal_greeting"])
        return 1.5 * features["hits_request"] + features["body_question_count"] / 3 + features["subject_has_question"] + 1.5 * features["is_reply"] + features["body_has_quoted_reply"] + human - 2 * bulk
    raise BatchError("Unsupported priority focus.")

def build_candidates(rows: list[dict[str, str]], frozen_ids: set[str], *, urgent: int = 8, needs_reply: int = 14, seed: int = 20261106, max_per_domain: int = 4) -> tuple[list[dict[str, str]], dict[str, int]]:
    if urgent < 0 or needs_reply < 0 or max_per_domain < 1:
        raise BatchError("Requested counts must be non-negative and max-per-domain must be positive.")
    eligible = [r for r in rows if (r.get("email_id") or "").strip() and r["email_id"].strip() not in frozen_ids and not (r.get("true_priority") or "").strip()]
    ids = [r["email_id"].strip() for r in eligible]
    if len(ids) != len(set(ids)):
        raise BatchError("Duplicate email IDs found in eligible source rows; resolve duplicates before sampling.")
    by_id = {r["email_id"].strip(): r for r in eligible}
    feature_by_id = {key: extract_features(row) for key, row in by_id.items()}
    domain_by_id = {key: (parse_sender(row.get("sender")).registered_domain or "(unknown)") for key, row in by_id.items()}
    rng = random.Random(seed)
    chosen: list[dict[str, str]] = []
    used: set[str] = set()
    summary: dict[str, int] = {}
    for label, wanted in (("URGENT", urgent), ("NEEDS_REPLY", needs_reply)):
        ranked = list(by_id)
        rng.shuffle(ranked)
        ranked.sort(key=lambda key: score_priority(label, feature_by_id[key]), reverse=True)
        domains: defaultdict[str, int] = defaultdict(int)
        selected = 0
        for key in ranked:
            if selected >= wanted:
                break
            if key in used or domains[domain_by_id[key]] >= max_per_domain:
                continue
            used.add(key); domains[domain_by_id[key]] += 1; selected += 1
            row = by_id[key]
            chosen.append({"email_id": key, "sender": row.get("sender", ""), "subject": row.get("subject", ""), "snippet": row.get("snippet", ""), "body_preview": row.get("body_text") or row.get("snippet", ""), "focus_class": label})
        summary[label] = selected
    return chosen, summary

def write_workbook(rows: list[dict[str, str]], output: Path, *, guide_path: Path = GUIDE, overwrite: bool = False) -> None:
    if output.exists() and not overwrite:
        raise BatchError("Output already exists; choose another name or pass --overwrite.")
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        backup_dir = output.parent.parent / "backups"; backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        shutil.copy2(output, backup_dir / f"{output.stem}.{stamp}{output.suffix}")
    wb = Workbook(); ws = wb.active; ws.title = "Priority Labeling"
    ws.append(FIELDS)
    header = PatternFill("solid", fgColor="1F4E78")
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF"); cell.fill = header; cell.protection = Protection(locked=True)
    for i, row in enumerate(rows, 1):
        ws.append([i, row["email_id"], row["sender"], row["subject"], row["snippet"], row["body_preview"][:1500], "", "no", ""])
        for col in range(2, 7):
            c = ws.cell(i + 1, col); c.number_format = "@"; c.data_type = "s"; c.protection = Protection(locked=True); c.alignment = Alignment(wrap_text=True, vertical="top")
        for col in range(7, 10):
            ws.cell(i + 1, col).protection = Protection(locked=False)
    ws.freeze_panes = "B2"; ws.auto_filter.ref = f"A1:I{ws.max_row}"; ws.protection.sheet = True
    ws.protection.selectLockedCells = False; ws.protection.selectUnlockedCells = False
    for col, width in {"A":9,"B":25,"C":32,"D":36,"E":42,"F":65,"G":20,"H":12,"I":40}.items(): ws.column_dimensions[col].width = width
    lists = wb.create_sheet("Lists"); lists.append(["priority", "unsure"])
    for i, val in enumerate(("URGENT", "NEEDS_REPLY", "INFORMATIONAL"), 2): lists.cell(i, 1, val)
    for i, val in enumerate(("yes", "no"), 2): lists.cell(i, 2, val)
    lists.sheet_state = "hidden"
    for col, formula in (("G", "'Lists'!$A$2:$A$4"), ("H", "'Lists'!$B$2:$B$3")):
        dv = DataValidation(type="list", formula1=formula, allow_blank=True); dv.showErrorMessage = True; ws.add_data_validation(dv); dv.add(f"{col}2:{col}{max(2, ws.max_row)}")
    guide = wb.create_sheet("Guide")
    if guide_path.is_file():
        for line in guide_path.read_text(encoding="utf-8").splitlines(): guide.append([line])
    else: guide.append(["Consult docs/EMAIL_LABELING_GUIDE.md before labeling."])
    guide.column_dimensions["A"].width = 120
    for row in guide.iter_rows():
        for cell in row: cell.alignment = Alignment(wrap_text=True, vertical="top")
    progress = wb.create_sheet("Progress"); progress.append(["Metric", "Value"]); progress.append(["Rows", f"=COUNTA('Priority Labeling'!A2:A{ws.max_row})"]); progress.append(["Completed", f'=COUNTIF(\'Priority Labeling\'!G2:G{ws.max_row},"<>")'])
    temp = output.with_name(output.stem + ".tmp" + output.suffix)
    try: wb.save(temp); temp.replace(output)
    finally:
        if temp.exists(): temp.unlink()

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", type=Path, default=DEFAULT_INPUT); p.add_argument("--split-manifest", type=Path, default=DEFAULT_SPLIT)
    p.add_argument("--output", type=Path, default=DEFAULT_OUTPUT); p.add_argument("--urgent", type=int, default=8); p.add_argument("--needs-reply", type=int, default=14)
    p.add_argument("--seed", type=int, default=20261106); p.add_argument("--max-per-domain", type=int, default=4); p.add_argument("--overwrite", action="store_true")
    a = p.parse_args(argv)
    try:
        rows = read_rows(a.input); frozen = read_frozen_test_ids(a.split_manifest)
        candidates, counts = build_candidates(rows, frozen, urgent=a.urgent, needs_reply=a.needs_reply, seed=a.seed, max_per_domain=a.max_per_domain)
        write_workbook(candidates, a.output, overwrite=a.overwrite)
    except (BatchError, OSError, ValueError) as exc:
        print(f"Batch preparation failed: {type(exc).__name__}; no labels were changed.", file=sys.stderr); return 2
    print(json.dumps({"candidate_rows": len(candidates), "requested": {"URGENT": a.urgent, "NEEDS_REPLY": a.needs_reply}, "selected_by_focus": counts, "frozen_ids_excluded": len(frozen), "output": str(a.output), "labels_assigned": 0}, indent=2))
    return 0

if __name__ == "__main__": raise SystemExit(main())
