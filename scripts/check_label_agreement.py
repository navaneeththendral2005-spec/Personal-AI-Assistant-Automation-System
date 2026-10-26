"""Sample verified development emails for blind relabeling and score agreement.

Sample (creates a blind review CSV suitable for scripts.label_emails):
    python -m scripts.check_label_agreement sample --input data/email_labels.csv \
        --split-manifest data/priority_engine_split.json --manifest data/relabel_manifest.json \
        --output data/relabel_review.csv --sample-size 30 --seed 20261027

After relabeling true_* fields in the review CSV, score agreement:
    python -m scripts.check_label_agreement score --input data/email_labels.csv \
        --review data/relabel_review.csv --output reports/label_agreement.json

Frozen test IDs are excluded from the relabel sample. The review CSV hides the
original labels so the second annotation is blind. Reports contain counts/kappa,
not message content or identifiers.
"""
from __future__ import annotations

import argparse
import csv
import json
import random
from pathlib import Path
from typing import Any

from scripts.sample_test_v2 import (
    CONTEXT_FIELDS,
    MANIFEST_VERSION,
    TRUTH_FIELDS,
    _atomic_write_json,
    has_any_human_label,
    read_csv,
    read_frozen_test_ids,
)

PRIORITY_LABELS = {"URGENT", "NEEDS_REPLY", "INFORMATIONAL", "SPAM"}
RESPONSE_LABELS = {"yes", "no"}
CATEGORY_LABELS = {"JOB", "FINANCE", "PERSONAL", "PROMOTIONS", "UPDATES", "OTHER"}
SPAM_LABELS = {"yes", "no"}
TASKS = {
    "is_spam": ("true_is_spam", "true_is_spam", SPAM_LABELS, "spam"),
    "priority": ("true_priority", "true_priority", PRIORITY_LABELS, "priority"),
    "needs_response": ("true_needs_response", "true_needs_response", RESPONSE_LABELS, "response"),
    "category": ("true_category", "true_category", CATEGORY_LABELS, "category"),
}


def _norm(value: str | None, task: str) -> str:
    value = (value or "").strip()
    if task in {"is_spam", "needs_response"}:
        return value.lower()
    return value.upper()


def _first_label(row: dict[str, str], task: str, valid: set[str]) -> tuple[str, str]:
    """Return first annotation and provenance; legacy spam is derived per schema rule."""
    if task == "is_spam":
        explicit = _norm(row.get("true_is_spam"), task)
        if explicit in valid:
            return explicit, "explicit_true_is_spam"
        priority = (row.get("true_priority") or "").strip().upper()
        if priority == "SPAM":
            return "yes", "derived_from_legacy_true_priority"
        if priority in PRIORITY_LABELS:
            return "no", "derived_from_legacy_true_priority"
        return "", "unavailable"
    value = _norm(row.get(TASKS[task][0]), task)
    return (value, "human_true_label") if value in valid else ("", "unavailable")


def _atomic_write_csv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    try:
        with temp.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        temp.replace(path)
    finally:
        if temp.exists():
            temp.unlink()


def sample_relabel_queue(
    input_path: Path,
    *,
    split_manifest_path: Path,
    manifest_path: Path,
    output_path: Path,
    sample_size: int = 30,
    seed: int = 20261027,
) -> dict[str, Any]:
    if sample_size < 1:
        raise ValueError("sample_size must be positive")
    _, rows = read_csv(input_path)
    by_id: dict[str, dict[str, str]] = {}
    for row in rows:
        email_id = (row.get("email_id") or "").strip()
        if not email_id:
            raise ValueError("Every source row must have a non-empty email_id")
        if email_id in by_id:
            raise ValueError("Input CSV contains duplicate email_id values")
        by_id[email_id] = row

    frozen_ids = read_frozen_test_ids(split_manifest_path)
    eligible = sorted(
        email_id for email_id, row in by_id.items()
        if has_any_human_label(row) and email_id not in frozen_ids
    )

    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Relabel manifest is unreadable: {manifest_path}") from exc
        selected = manifest.get("email_ids")
        if manifest.get("version") != MANIFEST_VERSION or not isinstance(selected, list):
            raise ValueError("Malformed or unsupported relabel manifest")
        if len(selected) != sample_size or len(selected) != len(set(selected)):
            raise ValueError("Existing relabel manifest size differs or contains duplicate IDs; refusing to resample")
        if set(selected) & frozen_ids:
            raise ValueError("Relabel manifest overlaps frozen test IDs; refusing to continue")
        if not set(selected).issubset(set(eligible)):
            raise ValueError("Relabel manifest contains IDs no longer eligible as verified development emails")
        created = False
    else:
        if len(eligible) < sample_size:
            raise ValueError(f"Only {len(eligible)} verified development emails are eligible; {sample_size} requested")
        selected = random.Random(seed).sample(eligible, sample_size)
        manifest = {
            "version": MANIFEST_VERSION,
            "sample_name": "relabel_consistency_v1",
            "seed": seed,
            "sample_size": sample_size,
            "email_ids": selected,
            "policy": "Private IDs only. Excludes all IDs in the existing frozen test manifest.",
        }
        _atomic_write_json(manifest_path, manifest)
        created = True

    if output_path.exists():
        _, existing_rows = read_csv(output_path)
        existing_ids = [(row.get("email_id") or "").strip() for row in existing_rows]
        if len(existing_ids) != len(set(existing_ids)) or set(existing_ids) != set(selected):
            raise ValueError("Existing relabel CSV does not match the frozen manifest; it was not modified")
        output_created = False
    else:
        review_fields = list(CONTEXT_FIELDS) + list(TRUTH_FIELDS)
        review_rows = []
        for email_id in selected:
            source = by_id[email_id]
            review_row = {field: (source.get(field) or "") for field in CONTEXT_FIELDS}
            review_row.update({field: "" for field in TRUTH_FIELDS})
            review_rows.append(review_row)
        _atomic_write_csv(output_path, review_fields, review_rows)
        output_created = True

    return {
        "sample_name": "relabel_consistency_v1",
        "sample_size": len(selected),
        "eligible_verified_development_count": len(eligible),
        "frozen_test_ids_excluded_count": len(frozen_ids),
        "manifest_created": created,
        "review_file_created": output_created,
        "manifest_path": str(manifest_path),
        "review_path": str(output_path),
    }


def cohen_kappa(first: list[str], second: list[str]) -> float | None:
    """Compute unweighted Cohen's kappa; return None when there are no pairs."""
    if len(first) != len(second):
        raise ValueError("Annotation lists must have equal length")
    if not first:
        return None
    labels = sorted(set(first) | set(second))
    n = len(first)
    observed = sum(a == b for a, b in zip(first, second)) / n
    expected = sum(
        (sum(value == label for value in first) / n)
        * (sum(value == label for value in second) / n)
        for label in labels
    )
    if expected == 1.0:
        return 1.0 if observed == 1.0 else None
    return (observed - expected) / (1.0 - expected)


def score_agreement(input_path: Path, review_path: Path) -> dict[str, Any]:
    _, source_rows = read_csv(input_path)
    _, review_rows = read_csv(review_path)
    source_by_id = {(row.get("email_id") or "").strip(): row for row in source_rows}
    review_by_id = {(row.get("email_id") or "").strip(): row for row in review_rows}
    if len(review_by_id) != len(review_rows):
        raise ValueError("Relabel review CSV contains blank or duplicate email IDs")
    missing = set(review_by_id) - set(source_by_id)
    if missing:
        raise ValueError(f"Review CSV contains {len(missing)} IDs absent from the source dataset")

    task_results: dict[str, Any] = {}
    for task, (first_column, second_column, valid, _) in TASKS.items():
        first_values: list[str] = []
        second_values: list[str] = []
        for email_id, second_row in review_by_id.items():
            first_row = source_by_id[email_id]
            first, provenance = _first_label(first_row, task, valid)
            second = _norm(second_row.get(second_column), task)
            if first in valid and second in valid:
                first_values.append(first)
                second_values.append(second)
        kappa = cohen_kappa(first_values, second_values)
        task_results[task] = {
            "paired_annotations": len(first_values),
            "cohen_kappa": round(kappa, 4) if kappa is not None else None,
            "status": "computed" if kappa is not None else "undefined_or_no_pairs",
        }
        if task == "is_spam":
            task_results[task]["first_annotation_policy"] = (
                "Uses explicit true_is_spam when valid; otherwise derives from legacy true_priority. "
                "Kappa is provisional for rows using the legacy-derived value."
            )
            task_results[task]["legacy_derived_pairs"] = sum(
                1 for email_id, second_row in review_by_id.items()
                if _first_label(source_by_id[email_id], task, valid)[1] == "derived_from_legacy_true_priority"
                and _norm(second_row.get(second_column), task) in valid
            )

    return {
        "method": "Cohen's kappa (unweighted)",
        "reviewed_emails": len(review_rows),
        "privacy_note": "Aggregate counts and agreement only; no email IDs or message text.",
        "tasks": task_results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    sample = subparsers.add_parser("sample", help="Create/resume a blind development relabel queue")
    sample.add_argument("--input", type=Path, default=Path("data/email_labels.csv"))
    sample.add_argument("--split-manifest", type=Path, default=Path("data/priority_engine_split.json"))
    sample.add_argument("--manifest", type=Path, default=Path("data/relabel_manifest.json"))
    sample.add_argument("--output", type=Path, default=Path("data/relabel_review.csv"))
    sample.add_argument("--sample-size", type=int, default=30)
    sample.add_argument("--seed", type=int, default=20261027)

    score = subparsers.add_parser("score", help="Compute per-task Cohen's kappa")
    score.add_argument("--input", type=Path, default=Path("data/email_labels.csv"))
    score.add_argument("--review", type=Path, default=Path("data/relabel_review.csv"))
    score.add_argument("--output", type=Path, default=Path("reports/label_agreement.json"))

    args = parser.parse_args()
    try:
        if args.command == "sample":
            result = sample_relabel_queue(
                args.input, split_manifest_path=args.split_manifest,
                manifest_path=args.manifest, output_path=args.output,
                sample_size=args.sample_size, seed=args.seed,
            )
            print(json.dumps(result, indent=2))
            print("The review CSV hides original labels. Keep both manifest and review CSV private.")
        else:
            result = score_agreement(args.input, args.review)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            temp = args.output.with_suffix(args.output.suffix + ".tmp")
            try:
                temp.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
                temp.replace(args.output)
            finally:
                if temp.exists():
                    temp.unlink()
            print(json.dumps(result, indent=2))
            print(f"Aggregate-only report written to {args.output}")
    except (FileNotFoundError, ValueError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
