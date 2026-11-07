"""Read-only aggregate audit for FRIDAY minority-labeling batches."""
from __future__ import annotations
import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from openpyxl import load_workbook

DEFAULT_DATASET = Path("data/email_labels_evaluation.csv")
DEFAULT_MANIFEST = Path("data/priority_engine_split.json")
EXPECTED_HEADERS = (
    "row_number", "email_id", "sender", "subject", "snippet", "body_preview",
    "is_spam", "needs_response", "priority", "category", "unsure", "note",
)
YES_NO = {"yes", "no"}
PRIORITIES = {"URGENT", "NEEDS_REPLY", "INFORMATIONAL"}
CATEGORIES = {"JOB", "FINANCE", "PROMOTIONS", "UPDATES", "OTHER"}


class AuditError(Exception):
    pass


def read_csv(path):
    if not path.is_file():
        raise AuditError(f"Dataset not found: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        headers, rows = list(reader.fieldnames or []), list(reader)
    if not headers or len(headers) != len(set(headers)) or "email_id" not in headers:
        raise AuditError("Dataset headers are missing, duplicated, or lack email_id.")
    indexed = {}
    missing = duplicates = 0
    for row in rows:
        key = (row.get("email_id") or "").strip()
        if not key:
            missing += 1
        elif key in indexed:
            duplicates += 1
        else:
            indexed[key] = row
    if missing or duplicates:
        raise AuditError(f"Dataset identity failure: {missing} missing IDs, {duplicates} duplicates.")
    return indexed, headers


def read_manifest(path):
    if not path.is_file():
        raise AuditError("Frozen split manifest is missing.")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AuditError("Frozen split manifest is unreadable.") from exc
    if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("tasks"), dict):
        raise AuditError("Frozen split manifest is malformed or unsupported.")
    result = {}
    for task in ("category", "priority"):
        entry = data["tasks"].get(task)
        ids = entry.get("test_ids") if isinstance(entry, dict) else None
        if not isinstance(ids, list) or any(not isinstance(x, str) or not x.strip() for x in ids) or len(ids) != len(set(ids)):
            raise AuditError(f"Frozen {task} test assignment is missing or malformed.")
        result[task] = set(ids)
    return result


def metadata(wb):
    if "Progress" not in wb.sheetnames:
        raise AuditError("Progress sheet is missing.")
    meta = {}
    for row in wb["Progress"].iter_rows(min_row=2, max_col=2, values_only=True):
        if row and row[0] is not None:
            meta[str(row[0]).strip().lower()] = "" if row[1] is None else str(row[1]).strip()
    for key in ("batch name", "schema version", "guide version", "mode"):
        if not meta.get(key):
            raise AuditError(f"Progress metadata missing: {key}.")
    if meta["schema version"] != "v2.1" or meta["guide version"] != "v2.1" or meta["mode"].lower() != "minority":
        raise AuditError("Expected v2.1 schema, v2.1 guide, and minority mode.")
    return meta


def audit(path, dataset, frozen_ids, target):
    if not path.is_file() or path.suffix.lower() != ".xlsx":
        raise AuditError(f"Workbook missing or not .xlsx: {path.name}")
    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        if not {"Labeling", "Guide"}.issubset(wb.sheetnames):
            raise AuditError(f"{path.name}: Labeling or Guide sheet missing.")
        meta = metadata(wb)
        guide_text = " ".join(str(c) for row in wb["Guide"].iter_rows(values_only=True) for c in row if c is not None)
        if "v2.1" not in guide_text:
            raise AuditError(f"{path.name}: Guide sheet does not identify v2.1.")
        ws = wb["Labeling"]
        headers = tuple(str(c.value or "").strip() for c in ws[1])
        if headers != EXPECTED_HEADERS:
            raise AuditError(f"{path.name}: unsupported Labeling headers.")

        counts = Counter()
        labels = Counter()
        seen = {}
        conflicts = set()
        all_ids = set()
        for values in ws.iter_rows(min_row=2, values_only=True):
            if not values or all(v is None for v in values):
                continue
            counts["workbook_rows"] += 1
            email_id = str(values[1] or "").strip()
            if not email_id:
                counts["missing_workbook_ids"] += 1
                continue
            all_ids.add(email_id)
            row = {
                "is_spam": str(values[6] or "").strip().lower(),
                "needs_response": str(values[7] or "").strip().lower(),
                "priority": str(values[8] or "").strip().upper(),
                "category": str(values[9] or "").strip().upper(),
                "unsure": str(values[10] or "").strip().lower(),
            }
            signature = tuple(row[k] for k in ("is_spam", "needs_response", "priority", "category", "unsure"))
            if email_id in seen:
                if seen[email_id] == signature:
                    counts["duplicate_rows"] += 1
                else:
                    conflicts.add(email_id)
                continue
            seen[email_id] = signature
            if email_id not in dataset:
                counts["ids_not_in_dataset"] += 1
            if email_id in frozen_ids:
                counts["frozen_test_overlap"] += 1

            if (row["is_spam"] not in YES_NO or row["needs_response"] not in YES_NO
                or row["priority"] not in PRIORITIES or row["category"] not in CATEGORIES
                or row["unsure"] not in YES_NO):
                counts["invalid_rows"] += 1
                continue
            if row["unsure"] == "yes":
                counts["uncertain_excluded"] += 1
                continue
            if ((row["needs_response"] == "yes" and row["priority"] == "INFORMATIONAL")
                or (row["needs_response"] == "no" and row["priority"] == "NEEDS_REPLY")):
                counts["consistency_conflicts"] += 1
                continue
            counts["verified_candidate_rows"] += 1
            labels[row[target]] += 1
            truth_col = "true_category" if target == "category" else "true_priority"
            truth = (dataset.get(email_id, {}).get(truth_col) or "").strip().upper()
            if truth and truth != row[target]:
                counts["existing_truth_conflicts"] += 1

        counts["unique_workbook_ids"] = len(seen)
        counts["conflicting_ids"] = len(conflicts)
        return meta, counts, labels
    finally:
        wb.close()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    p.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    p.add_argument("--category-workbook", type=Path, default=Path("data/labeling/category_batch1.xlsx"))
    p.add_argument("--priority-workbook", type=Path, default=Path("data/labeling/priority_batch2.xlsx"))
    args = p.parse_args()
    try:
        dataset, columns = read_csv(args.dataset)
        if not {"true_category", "true_priority"}.issubset(columns):
            raise AuditError("Dataset lacks true_category or true_priority.")
        frozen = read_manifest(args.manifest)
        results = [
            ("category", args.category_workbook, *audit(args.category_workbook, dataset, frozen["category"], "category")),
            ("priority", args.priority_workbook, *audit(args.priority_workbook, dataset, frozen["priority"], "priority")),
        ]
    except (AuditError, OSError, csv.Error) as exc:
        print(f"AUDIT STOPPED: {exc}")
        return 2

    blocked = False
    print("READ-ONLY LABELING BATCH AUDIT")
    print(f"Dataset rows: {len(dataset)}")
    print("No files were modified.")
    blocker_keys = ("missing_workbook_ids", "duplicate_rows", "conflicting_ids", "ids_not_in_dataset",
                    "frozen_test_overlap", "invalid_rows", "consistency_conflicts", "existing_truth_conflicts")
    for task, path, meta, counts, labels in results:
        has_blockers = any(counts[k] for k in blocker_keys)
        blocked |= has_blockers
        print(f"\n{path.name} | task={task} | batch={meta['batch name']} | mode={meta['mode']}")
        print(f"  schema/guide: {meta['schema version']}/{meta['guide version']}")
        for key in ("workbook_rows", "unique_workbook_ids", "verified_candidate_rows", "uncertain_excluded", *blocker_keys):
            print(f"  {key}: {counts[key]}")
        print("  target label counts:")
        for label, count in sorted(labels.items()):
            print(f"    {label}: {count}")
        print("  result:", "BLOCKED — resolve findings" if has_blockers else "PASS — no blockers found")
    print("\nOverall:", "BLOCKED — do not import" if blocked else "PASS — ready for import review")
    return 1 if blocked else 0


if __name__ == "__main__":
    raise SystemExit(main())
