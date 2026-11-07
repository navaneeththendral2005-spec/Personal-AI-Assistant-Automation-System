"""Merge task-specific minority human annotations into FRIDAY's canonical dataset.

Default mode is a read-only preview. Use --apply to back up and atomically update
 data/email_labels_evaluation.csv. Only the task-specific field is copied:
 category_batch1 -> true_category; priority_batch2 -> true_priority.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from shutil import copy2

from openpyxl import load_workbook

ROOT = Path.cwd()
LEDGER = ROOT / "data" / "email_labels_v2.csv"
DATASET = ROOT / "data" / "email_labels_evaluation.csv"
MANIFEST = ROOT / "data" / "priority_engine_split.json"
CATEGORY_WORKBOOK = ROOT / "data" / "labeling" / "category_batch1.xlsx"
PRIORITY_WORKBOOK = ROOT / "data" / "labeling" / "priority_batch2.xlsx"
BATCHES = {
    "category_batch1": ("category", "true_category", {"JOB", "FINANCE", "PROMOTIONS", "UPDATES", "OTHER"}, 40),
    "priority_batch2": ("priority", "true_priority", {"URGENT", "NEEDS_REPLY", "INFORMATIONAL"}, 22),
}

class MergeError(ValueError):
    pass


def read_csv(path: Path, label: str):
    if not path.is_file():
        raise MergeError(f"Missing {label} file: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        headers = list(reader.fieldnames or [])
        if not headers or len(headers) != len(set(headers)):
            raise MergeError(f"{label} has missing or duplicate headers.")
        rows = [{k: (v or "") for k, v in row.items()} for row in reader]
    if any(None in row for row in rows):
        raise MergeError(f"{label} contains rows wider than its header.")
    return headers, rows


def unique_index(rows, label):
    index = {}
    for row in rows:
        key = (row.get("email_id") or "").strip()
        if not key or key in index:
            raise MergeError(f"{label} contains a missing or duplicate email_id; no changes made.")
        index[key] = row
    return index


def is_unsure(value) -> bool:
    """Normalize Excel text, booleans, and numeric checkbox values safely."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if value in (0, 1):
            return bool(value)
        raise MergeError("Workbook contains an unsupported numeric value in the unsure column.")
    token = str(value).strip().casefold()
    if token in {"yes", "y", "true", "1", "unsure", "uncertain"}:
        return True
    if token in {"no", "n", "false", "0", "certain"}:
        return False
    raise MergeError(f"Workbook contains an unrecognized unsure value: {token!r}.")


def inspect_workbook(path: Path, batch_name: str, expected_rows: int, expected_uncertain: int):
    """Read workbook row counts directly; uncertain rows are intentionally absent from the ledger."""
    if not path.is_file():
        raise MergeError(f"Missing source workbook for uncertainty audit: {path}")
    try:
        workbook = load_workbook(path, read_only=True, data_only=True)
    except Exception as exc:
        raise MergeError(f"Cannot read source workbook {path.name}.") from exc
    try:
        if "Labeling" not in workbook.sheetnames or "Progress" not in workbook.sheetnames:
            raise MergeError(f"{path.name} must contain Labeling and Progress sheets.")
        sheet = workbook["Labeling"]
        iterator = sheet.iter_rows(values_only=True)
        header = next(iterator, None)
        if not header:
            raise MergeError(f"{path.name} has an empty Labeling sheet.")
        positions = {str(value).strip(): i for i, value in enumerate(header) if value is not None}
        if not {"email_id", "unsure"}.issubset(positions):
            raise MergeError(f"{path.name} is missing email_id or unsure columns.")
        rows = []
        for values in iterator:
            if not values or all(value is None for value in values):
                continue
            email_id = str(values[positions["email_id"]] or "").strip()
            if not email_id:
                continue
            unsure_value = values[positions["unsure"]]
            rows.append((email_id, is_unsure(unsure_value)))
        ids = [email_id for email_id, _ in rows]
        if len(ids) != len(set(ids)):
            raise MergeError(f"{path.name} contains duplicate email IDs.")
        uncertain_ids = {email_id for email_id, unsure in rows if unsure}
        certain_ids = {email_id for email_id, unsure in rows if not unsure}
        if len(rows) != expected_rows or len(uncertain_ids) != expected_uncertain:
            raise MergeError(
                f"{batch_name}: workbook has {len(rows)} rows and {len(uncertain_ids)} uncertain; "
                f"expected {expected_rows} rows and {expected_uncertain} uncertain."
            )

        progress = workbook["Progress"]
        metadata = {}
        for values in progress.iter_rows(values_only=True):
            if len(values) >= 2 and values[0] is not None:
                key = str(values[0]).strip().casefold().replace("_", " ")
                metadata[key] = str(values[1] or "").strip()
        expected_meta = {
            "batch name": batch_name,
            "mode": "MINORITY",
            "schema version": "v2.1",
            "guide version": "v2.1",
        }
        for key, expected in expected_meta.items():
            if metadata.get(key, "").casefold() != expected.casefold():
                raise MergeError(f"{path.name} Progress metadata mismatch for {key}.")
        return certain_ids, uncertain_ids, len(rows)
    finally:
        workbook.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, default=LEDGER)
    parser.add_argument("--dataset", type=Path, default=DATASET)
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--category-workbook", type=Path, default=CATEGORY_WORKBOOK)
    parser.add_argument("--priority-workbook", type=Path, default=PRIORITY_WORKBOOK)
    parser.add_argument("--apply", action="store_true", help="Back up and update the canonical dataset. Default is preview only.")
    args = parser.parse_args(argv)

    try:
        headers, dataset_rows = read_csv(args.dataset, "canonical evaluation dataset")
        ledger_headers, ledger_rows = read_csv(args.ledger, "human annotation ledger")
        if "email_id" not in headers or "email_id" not in ledger_headers:
            raise MergeError("Both files must contain email_id.")
        dataset_by_id = unique_index(dataset_rows, "Canonical evaluation dataset")
        if not dataset_rows:
            raise MergeError("Canonical evaluation dataset is empty.")

        try:
            manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise MergeError("Frozen split manifest is missing or unreadable; it will not be created or repaired.") from exc
        if not isinstance(manifest, dict) or manifest.get("version") != 1 or not isinstance(manifest.get("tasks"), dict):
            raise MergeError("Frozen split manifest has an unsupported schema.")

        updates = {"category": {}, "priority": {}}
        uncertain_counts = Counter()
        workbook_paths = {
            "category_batch1": args.category_workbook,
            "priority_batch2": args.priority_workbook,
        }
        workbook_expected = {
            "category_batch1": (40, 0),
            "priority_batch2": (30, 8),
        }
        workbook_ids = {}
        for batch_name, workbook_path in workbook_paths.items():
            total, uncertain_expected = workbook_expected[batch_name]
            certain_ids, uncertain_ids, workbook_total = inspect_workbook(
                workbook_path, batch_name, total, uncertain_expected
            )
            workbook_ids[batch_name] = (certain_ids, uncertain_ids)
            uncertain_counts[batch_name] = len(uncertain_ids)

        for batch_name, (task, target, allowed, expected) in BATCHES.items():
            batch_rows = [r for r in ledger_rows if (r.get("batch_name") or "").strip() == batch_name]
            if not batch_rows:
                raise MergeError(f"Expected batch {batch_name!r} is absent from the ledger.")
            seen = set()
            for row in batch_rows:
                if (row.get("labeler") or "").strip() != "human":
                    raise MergeError(f"Batch {batch_name!r} contains non-human provenance.")
                if (row.get("schema_version") or "").strip() != "v2.1" or (row.get("guide_version") or "").strip() != "v2.1":
                    raise MergeError(f"Batch {batch_name!r} is not v2.1.")
                if (row.get("label_round") or "").strip() != "minority":
                    raise MergeError(f"Batch {batch_name!r} does not have minority label_round provenance.")
                email_id = (row.get("email_id") or "").strip()
                if not email_id or email_id in seen:
                    raise MergeError(f"Batch {batch_name!r} contains a missing or repeated email_id.")
                seen.add(email_id)
                if email_id not in dataset_by_id:
                    raise MergeError(f"Batch {batch_name!r} contains an email absent from the canonical dataset.")
                if is_unsure(row.get("unsure")):
                    raise MergeError(
                        f"Batch {batch_name!r} contains an uncertain row in the human ledger; "
                        "uncertain annotations must remain excluded."
                    )
                value = (row.get(task) or "").strip().upper()
                if value not in allowed:
                    raise MergeError(f"Batch {batch_name!r} contains an invalid verified {task} value.")
                updates[task][email_id] = value
            certain_ids, uncertain_ids = workbook_ids[batch_name]
            if set(updates[task]) != certain_ids:
                raise MergeError(
                    f"Batch {batch_name!r}: verified ledger IDs do not match certain workbook IDs."
                )
            if set(updates[task]) & uncertain_ids:
                raise MergeError(f"Batch {batch_name!r}: uncertain workbook rows appear in the ledger.")
            if len(updates[task]) != expected:
                raise MergeError(f"Batch {batch_name!r}: expected {expected} verified labels, found {len(updates[task])}.")

            task_entry = manifest["tasks"].get(task)
            test_ids = task_entry.get("test_ids") if isinstance(task_entry, dict) else None
            if not isinstance(test_ids, list) or not all(isinstance(x, str) and x.strip() for x in test_ids) or len(test_ids) != len(set(test_ids)):
                raise MergeError(f"Frozen {task} test assignments are missing or malformed.")
            overlap = set(updates[task]) & set(test_ids)
            if overlap:
                raise MergeError(f"{len(overlap)} new {task} annotations overlap the frozen test set; no changes made.")

        # Reject conflicting existing ground truth; same-value rows are idempotent.
        changed = Counter()
        already_same = Counter()
        for task, values in updates.items():
            target = "true_category" if task == "category" else "true_priority"
            for email_id, value in values.items():
                old = (dataset_by_id[email_id].get(target) or "").strip().upper()
                if old and old != value:
                    raise MergeError(f"A minority {task} annotation conflicts with existing canonical ground truth; no changes made.")
                if old == value:
                    already_same[task] += 1
                else:
                    changed[task] += 1

        out_headers = list(headers)
        for col in ("true_category", "true_priority"):
            if col not in out_headers:
                out_headers.append(col)
        for task, values in updates.items():
            target = "true_category" if task == "category" else "true_priority"
            for email_id, value in values.items():
                dataset_by_id[email_id][target] = value

        print("FRIDAY minority annotation merge preview" if not args.apply else "FRIDAY minority annotation merge")
        print(f"Dataset rows preserved: {len(dataset_rows)}")
        print(f"Category verified labels: {len(updates['category'])} (new {changed['category']}, already same {already_same['category']})")
        print(f"Priority verified labels: {len(updates['priority'])} (new {changed['priority']}, already same {already_same['priority']})")
        print(
            "Uncertain rows excluded: "
            f"{uncertain_counts['category_batch1'] + uncertain_counts['priority_batch2']} "
            f"(category {uncertain_counts['category_batch1']}, priority {uncertain_counts['priority_batch2']})"
        )
        print(f"Frozen test overlap: 0; manifest modified: no")
        print(f"Resulting verified category labels: {sum(bool((r.get('true_category') or '').strip()) for r in dataset_rows)}")
        print(f"Resulting verified priority labels: {sum(bool((r.get('true_priority') or '').strip()) for r in dataset_rows)}")

        if not args.apply:
            print("Preview only: no files changed. Re-run with --apply to back up and update the canonical dataset.")
            return 0

        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = args.dataset.with_name(f"{args.dataset.stem}.backup_{stamp}{args.dataset.suffix}")
        copy2(args.dataset, backup)
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8-sig", newline="", dir=args.dataset.parent, prefix=f".{args.dataset.name}.", suffix=".tmp", delete=False) as handle:
                temp_path = Path(handle.name)
                writer = csv.DictWriter(handle, fieldnames=out_headers, extrasaction="ignore", lineterminator="\n")
                writer.writeheader()
                writer.writerows(dataset_rows)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, args.dataset)
            temp_path = None
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
        print(f"Backup created: {backup.name}")
        print(f"Canonical dataset updated: {args.dataset}")
        return 0
    except (MergeError, OSError, csv.Error) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
