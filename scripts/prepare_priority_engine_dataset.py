"""Prepare a private evaluation CSV using final human adjudications.

Run from the project root:
    python -m scripts.prepare_priority_engine_dataset

The script reads data/email_labels.csv and
 data/label_disagreement_adjudication_final.csv, applies final adjudicated
labels by the project's established email_id identity strategy, and writes a
separate data/email_labels_evaluation.csv. Inputs are never modified and an
existing output is never overwritten.

The script never creates, repairs, or regenerates the frozen holdout manifest.
If a manifest exists, its schema, row membership, and task ground truth are
checked against the prepared dataset before output is written.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from scripts.evaluate_priority_engine import TASKS
from scripts.priority_engine_splits import MANIFEST_VERSION, row_key

PROJECT_ROOT = Path.cwd()
DEFAULT_INPUT = PROJECT_ROOT / "data" / "email_labels.csv"
DEFAULT_ADJUDICATION = PROJECT_ROOT / "data" / "label_disagreement_adjudication_final.csv"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "email_labels_evaluation.csv"
DEFAULT_MANIFEST = PROJECT_ROOT / "data" / "priority_engine_split.json"

FEATURE_COLUMNS = ("email_id", "sender", "subject", "snippet", "body_text")
TRUTH_COLUMNS = {
    "priority": "true_priority",
    "is_spam": "true_is_spam",
    "needs_response": "true_needs_response",
    "category": "true_category",
}
FINAL_COLUMNS = {
    "true_is_spam": "final_true_is_spam",
    "true_priority": "final_true_priority",
    "true_needs_response": "final_true_needs_response",
    "true_category": "final_true_category",
}
ADJUDICATION_REQUIRED = {
    "email_id",
    *FINAL_COLUMNS.values(),
    "decision_reason",
}

# These fields may be absent in older exports; blank columns are added so the
# downstream evaluator can score the available tasks without inventing values.
EVALUATION_COLUMNS = {
    "true_is_spam", "true_priority", "true_needs_response", "true_category",
    "rules_is_spam", "predicted_is_spam", "llm_is_spam",
    "rules_priority", "predicted_priority", "llm_priority",
    "rules_needs_response", "predicted_needs_response", "llm_needs_response",
    "rules_category", "predicted_category", "llm_category",
}

PRIORITY_ALIASES = {
    "urgent": "URGENT",
    "needs reply": "NEEDS_REPLY",
    "needs_reply": "NEEDS_REPLY",
    "needs-reply": "NEEDS_REPLY",
    "needsreply": "NEEDS_REPLY",
    "informational": "INFORMATIONAL",
}
CATEGORY_ALIASES = {
    "job": "JOB",
    "finance": "FINANCE",
    "personal": "PERSONAL",
    "promotions": "PROMOTIONS",
    "promotion": "PROMOTIONS",
    "update": "UPDATES",
    "updates": "UPDATES",
    "other": "OTHER",
}
YES_NO_ALIASES = {
    "yes": "yes", "y": "yes", "true": "yes",
    "no": "no", "n": "no", "false": "no",
}


class DatasetPreparationError(ValueError):
    """A safe-to-display input, schema, merge, or manifest validation error."""


def _read_csv(path: Path, *, description: str) -> tuple[list[str], list[dict[str, str]]]:
    if not path.is_file():
        raise DatasetPreparationError(f"{description} file not found: {path}")
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            headers = list(reader.fieldnames or [])
            if not headers:
                raise DatasetPreparationError(f"{description} CSV has no header row.")
            if len(headers) != len(set(headers)):
                raise DatasetPreparationError(f"{description} CSV has duplicate column names.")
            rows = list(reader)
    except (OSError, csv.Error) as exc:
        raise DatasetPreparationError(f"Could not read {description} CSV ({type(exc).__name__}).") from exc

    if any(None in row for row in rows):
        raise DatasetPreparationError(f"{description} CSV contains rows with more cells than its header.")
    return headers, [{key: (value or "") for key, value in row.items()} for row in rows]


def _index_by_email_id(
    rows: list[dict[str, str]], *, description: str,
) -> dict[str, dict[str, str]]:
    indexed: dict[str, dict[str, str]] = {}
    missing = 0
    duplicates = 0
    for row in rows:
        email_id = (row.get("email_id") or "").strip()
        if not email_id:
            missing += 1
            continue
        if email_id in indexed:
            duplicates += 1
            continue
        indexed[email_id] = row
    if missing or duplicates:
        raise DatasetPreparationError(
            f"{description} identity validation failed: {missing} missing email IDs; "
            f"{duplicates} duplicate email IDs. No output was written."
        )
    return indexed


def _normalize_final(value: str, target_column: str, *, row_number: int) -> str:
    raw = (value or "").strip()
    if target_column in {"true_is_spam", "true_needs_response"}:
        normalized = YES_NO_ALIASES.get(raw.casefold(), "")
    elif target_column == "true_priority":
        normalized = PRIORITY_ALIASES.get(raw.casefold(), "")
    else:
        normalized = CATEGORY_ALIASES.get(raw.casefold(), "")

    if not normalized:
        raise DatasetPreparationError(
            f"Adjudication row {row_number} has an invalid or missing {FINAL_COLUMNS[target_column]} value. "
            "No output was written."
        )
    allowed = {
        label.lower() if target_column in {"true_is_spam", "true_needs_response"} else label
        for label in TASKS[
            {"true_is_spam": "is_spam", "true_priority": "priority",
             "true_needs_response": "needs_response", "true_category": "category"}[target_column]
        ]["labels"]
    }
    comparison = normalized.lower() if target_column in {"true_is_spam", "true_needs_response"} else normalized
    if comparison not in allowed:
        raise DatasetPreparationError(
            f"Adjudication row {row_number} has an unsupported {FINAL_COLUMNS[target_column]} label. "
            "No output was written."
        )
    return normalized


def _normalize_existing_truth(value: str, task: str) -> str:
    value = (value or "").strip()
    return value.lower() if task in {"is_spam", "needs_response"} else value.upper()


def _validate_adjudications(
    rows: list[dict[str, str]],
) -> dict[str, dict[str, str]]:
    indexed = _index_by_email_id(rows, description="Adjudication")
    if not rows:
        raise DatasetPreparationError("Adjudication CSV contains no rows.")

    normalized: dict[str, dict[str, str]] = {}
    for row_number, (email_id, row) in enumerate(indexed.items(), start=2):
        if not (row.get("decision_reason") or "").strip():
            raise DatasetPreparationError(
                f"Adjudication row {row_number} has no decision_reason. No output was written."
            )
        final: dict[str, str] = {}
        for target, source_column in FINAL_COLUMNS.items():
            final[target] = _normalize_final(
                row.get(source_column, ""), target_column=target, row_number=row_number,
            ) if (row.get(source_column) or "").strip() else ""

        spam = final["true_is_spam"]
        priority = final["true_priority"]
        if spam == "yes" and priority:
            raise DatasetPreparationError(
                f"Adjudication row {row_number} marks spam=yes but also supplies a priority. "
                "No output was written."
            )
        if spam == "no" and not priority:
            raise DatasetPreparationError(
                f"Adjudication row {row_number} is non-spam but has no final priority. "
                "No output was written."
            )
        for target in ("true_needs_response", "true_category"):
            if not final[target]:
                raise DatasetPreparationError(
                    f"Adjudication row {row_number} has a missing {FINAL_COLUMNS[target]}. "
                    "No output was written."
                )
        normalized[email_id] = final
    return normalized


def _manifest_task_labels(task: str) -> tuple[str, ...]:
    config = TASKS[task]
    return tuple(config.get("split_labels", config["labels"]))


def validate_existing_manifest(
    manifest_path: Path,
    rows: list[dict[str, str]],
) -> dict[str, Any]:
    """Validate an existing frozen manifest without writing or changing it."""
    if not manifest_path.exists():
        return {"status": "missing_not_created", "tasks_checked": 0, "frozen_test_rows_checked": 0}
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DatasetPreparationError(
            "The existing split manifest is unreadable. Back it up and investigate it; "
            "this script will not repair or regenerate it."
        ) from exc

    if (
        not isinstance(manifest, dict)
        or manifest.get("version") != MANIFEST_VERSION
        or not isinstance(manifest.get("tasks"), dict)
    ):
        raise DatasetPreparationError(
            "The existing split manifest has an unsupported or malformed schema. "
            "It was not modified."
        )

    indexed = _index_by_email_id(rows, description="Prepared dataset")
    source_keys = [row_key(row) for row in rows]
    if len(source_keys) != len(set(source_keys)):
        raise DatasetPreparationError(
            "Prepared dataset has duplicate row keys; frozen split compatibility cannot be guaranteed."
        )

    task_count = 0
    frozen_count = 0
    for task, entry in manifest["tasks"].items():
        if task not in TASKS:
            raise DatasetPreparationError(
                "The existing split manifest contains an unsupported task assignment. "
                "It was not modified."
            )
        if not isinstance(entry, dict):
            raise DatasetPreparationError("The existing split manifest contains a malformed task entry.")
        config = TASKS[task]
        expected_truth = config["truth"]
        expected_labels = list(_manifest_task_labels(task))
        if entry.get("truth_column", expected_truth) != expected_truth:
            raise DatasetPreparationError(
                f"The existing split manifest has a truth-column mismatch for task {task!r}. It was not modified."
            )
        if entry.get("labels", expected_labels) != expected_labels:
            raise DatasetPreparationError(
                f"The existing split manifest has a label-schema mismatch for task {task!r}. It was not modified."
            )
        test_ids = entry.get("test_ids")
        if (
            not isinstance(test_ids, list)
            or not all(isinstance(item, str) and item.strip() for item in test_ids)
            or len(test_ids) != len(set(test_ids))
        ):
            raise DatasetPreparationError(
                f"The existing split manifest has malformed test IDs for task {task!r}. It was not modified."
            )
        missing = set(test_ids) - set(indexed)
        if missing:
            raise DatasetPreparationError(
                f"The existing frozen holdout has {len(missing)} row(s) missing from the prepared dataset "
                f"for task {task!r}. The manifest was not modified."
            )

        allowed = {
            label.lower() if task in {"is_spam", "needs_response"} else label.upper()
            for label in expected_labels
        }
        unverified = 0
        for email_id in test_ids:
            truth = _normalize_existing_truth(indexed[email_id].get(expected_truth, ""), task)
            # Legacy SPAM is intentionally not priority ground truth; the evaluator
            # separately excludes it from priority scoring.
            if task == "priority" and truth == "SPAM":
                continue
            if truth not in allowed:
                unverified += 1
        if unverified:
            raise DatasetPreparationError(
                f"The existing frozen holdout contains {unverified} row(s) without valid ground truth "
                f"for task {task!r}. Resolve the source labels without regenerating the manifest."
            )
        task_count += 1
        frozen_count += len(test_ids)

    return {
        "status": "passed",
        "tasks_checked": task_count,
        "frozen_test_rows_checked": frozen_count,
    }


def prepare_dataset(
    input_path: Path,
    adjudication_path: Path,
    output_path: Path,
    manifest_path: Path,
) -> dict[str, Any]:
    """Merge final adjudications into a new evaluation dataset and validate it."""
    if output_path.exists():
        raise DatasetPreparationError(
            f"Output already exists: {output_path}. Nothing was overwritten; choose a new output path."
        )

    source_headers, source_rows = _read_csv(input_path, description="Source dataset")
    adjudication_headers, adjudication_rows = _read_csv(adjudication_path, description="Adjudication")
    missing_features = sorted(set(FEATURE_COLUMNS) - set(source_headers))
    if missing_features:
        raise DatasetPreparationError(
            "Source dataset is missing required columns: " + ", ".join(missing_features) + "."
        )
    missing_adjudication = sorted(ADJUDICATION_REQUIRED - set(adjudication_headers))
    if missing_adjudication:
        raise DatasetPreparationError(
            "Adjudication CSV is missing required columns: " + ", ".join(missing_adjudication) + "."
        )
    if not source_rows:
        raise DatasetPreparationError("Source dataset contains no email rows.")

    source_by_id = _index_by_email_id(source_rows, description="Source dataset")
    final_by_id = _validate_adjudications(adjudication_rows)
    unknown_ids = set(final_by_id) - set(source_by_id)
    if unknown_ids:
        raise DatasetPreparationError(
            f"Adjudication CSV contains {len(unknown_ids)} email(s) absent from the source dataset. "
            "Identity matching is by email_id only; no output was written."
        )

    output_headers = list(source_headers)
    for column in sorted(EVALUATION_COLUMNS):
        if column not in output_headers:
            output_headers.append(column)

    merged_rows: list[dict[str, str]] = []
    applied_by_field: Counter[str] = Counter()
    changed_by_field: Counter[str] = Counter()
    adjudicated_ids = set(final_by_id)

    for source in source_rows:
        row = dict(source)
        for column in EVALUATION_COLUMNS:
            row.setdefault(column, "")
        email_id = (source.get("email_id") or "").strip()
        final = final_by_id.get(email_id)
        if final is not None:
            for target, value in final.items():
                old = (row.get(target) or "").strip()
                # A spam adjudication explicitly means priority is not applicable;
                # blank final_true_priority must clear any legacy SPAM/priority value.
                row[target] = value
                applied_by_field[target] += 1
                if old != value:
                    changed_by_field[target] += 1
        merged_rows.append(row)

    manifest_status = validate_existing_manifest(manifest_path, merged_rows)

    # Do not use predictions as labels: only the final_true_* adjudication fields
    # are copied into true_* columns. Existing true_* labels for other emails remain.
    output_path.parent.mkdir(parents=True, exist_ok=True)
    created_output = False
    try:
        with output_path.open("x", encoding="utf-8-sig", newline="") as handle:
            created_output = True
            writer = csv.DictWriter(
                handle,
                fieldnames=output_headers,
                extrasaction="raise",
                lineterminator="\n",
            )
            writer.writeheader()
            writer.writerows(merged_rows)
    except (OSError, csv.Error) as exc:
        if created_output:
            try:
                output_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise DatasetPreparationError(
            f"Could not write the evaluation dataset ({type(exc).__name__})."
        ) from exc

    truth_counts: dict[str, dict[str, int]] = {}
    for task, truth_column in TRUTH_COLUMNS.items():
        counts = Counter(
            _normalize_existing_truth(row.get(truth_column, ""), task)
            for row in merged_rows
            if (row.get(truth_column) or "").strip()
        )
        truth_counts[task] = dict(sorted(counts.items()))

    return {
        "source_rows": len(source_rows),
        "adjudication_rows": len(adjudication_rows),
        "unique_adjudicated_emails_matched": len(adjudicated_ids),
        "output_rows": len(merged_rows),
        "adjudicated_labels_applied_by_field": dict(sorted(applied_by_field.items())),
        "labels_changed_from_source_by_field": dict(sorted(changed_by_field.items())),
        "nonempty_truth_counts_by_task": truth_counts,
        "frozen_manifest": manifest_status,
        "policy": {
            "identity": "Exact email_id match; missing/duplicate IDs and unknown adjudication IDs are rejected.",
            "precedence": "Final human-adjudicated labels override source true_* labels for matched emails.",
            "predictions": "predicted_*, rules_*, and llm_* columns are preserved and never used as ground truth.",
            "spam_priority": "Priority is blank when final_true_is_spam=yes; spam is evaluated by its own task.",
            "manifest": "Existing manifest is read-only. This script never creates, repairs, or regenerates it.",
            "provenance": "Existing true_* labels are preserved by project convention but their human provenance is not independently verified by this script; final adjudication labels are validated and take precedence.",
            "privacy": "Console output contains aggregate counts only; email IDs and message content are not printed.",
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="Existing private email-label dataset.")
    parser.add_argument("--adjudication", type=Path, default=DEFAULT_ADJUDICATION, help="Validated final adjudication CSV.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="New private evaluation CSV; must not already exist.")
    parser.add_argument("--split-manifest", type=Path, default=DEFAULT_MANIFEST, help="Existing private holdout manifest; never modified.")
    args = parser.parse_args(argv)

    try:
        summary = prepare_dataset(args.input, args.adjudication, args.output, args.split_manifest)
    except (DatasetPreparationError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print("FRIDAY evaluation dataset prepared successfully.")
    print(f"Source rows: {summary['source_rows']}")
    print(f"Adjudication rows: {summary['adjudication_rows']}")
    print(f"Matched adjudicated emails: {summary['unique_adjudicated_emails_matched']}")
    print(f"Output rows: {summary['output_rows']}")
    print(f"Adjudicated labels applied by field: {summary['adjudicated_labels_applied_by_field']}")
    print(f"Labels changed from source by field: {summary['labels_changed_from_source_by_field']}")
    print(f"Non-empty true-label counts by task: {summary['nonempty_truth_counts_by_task']}")
    manifest = summary["frozen_manifest"]
    print(f"Frozen manifest status: {manifest['status']}")
    if manifest["status"] == "missing_not_created":
        print("WARNING: No split manifest was found; none was created. Holdout compatibility was not checked.")
    else:
        print(f"Frozen manifest tasks checked: {manifest['tasks_checked']}")
        print(f"Frozen test assignments checked: {manifest['frozen_test_rows_checked']}")
    print(f"Output: {args.output}")
    print("Privacy: aggregate counts only; no email IDs or message content printed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
