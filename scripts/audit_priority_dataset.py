"""Audit FRIDAY's canonical verified-label dataset.

Run from the project root:

    python -m scripts.audit_priority_dataset
    python -m scripts.audit_priority_dataset --write-doc docs/PRIORITY_ENGINE_DATASET.md

Reports, per task: verified-label counts, class distribution, imbalance ratio,
under-represented classes, how many more labels each class needs to reach a target,
the development/frozen-test split sizes (manifest is read-only), data-integrity
problems, and label provenance. Output is aggregate only: no email IDs, senders,
subjects, or bodies.

The canonical dataset is ``data/email_labels_evaluation.csv`` (produced by
``scripts.prepare_priority_engine_dataset`` from ``data/email_labels.csv`` plus the
final human adjudications). Anything else is a working copy or a backup.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from scripts.evaluate_feature_pipeline import FeatureEvaluationError, read_frozen_test_ids
from scripts.evaluate_priority_engine import TASKS, _norm, _truth_key
from scripts.priority_engine_splits import row_key

PROJECT_ROOT = Path.cwd()
DEFAULT_INPUT = PROJECT_ROOT / "data" / "email_labels_evaluation.csv"
DEFAULT_MANIFEST = PROJECT_ROOT / "data" / "priority_engine_split.json"
DEFAULT_ADJUDICATION = PROJECT_ROOT / "data" / "label_disagreement_adjudication_final.csv"
DEFAULT_JSON = PROJECT_ROOT / "reports" / "dataset_audit.json"
DEFAULT_MIN_PER_CLASS = 10
DEFAULT_TARGET_PER_CLASS = 20


class AuditError(ValueError):
    """A safe-to-display input problem."""


def _read(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    if not path.is_file():
        raise AuditError(f"File not found: {path}")
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            headers = list(reader.fieldnames or [])
            rows = [{k: (v or "") for k, v in row.items() if k is not None} for row in reader]
    except (OSError, csv.Error) as exc:
        raise AuditError(f"Could not read {path.name} ({type(exc).__name__}).") from exc
    if not headers:
        raise AuditError(f"{path.name} has no header row.")
    return headers, rows


def _file_fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()[:16]


def audit_dataset(
    input_path: Path,
    *,
    manifest_path: Path | None = None,
    adjudication_path: Path | None = None,
    min_per_class: int = DEFAULT_MIN_PER_CLASS,
    target_per_class: int = DEFAULT_TARGET_PER_CLASS,
) -> dict[str, Any]:
    headers, rows = _read(input_path)

    ids = [(row.get("email_id") or "").strip() for row in rows]
    id_counts = Counter(i for i in ids if i)
    integrity: dict[str, Any] = {
        "rows": len(rows),
        "missing_email_ids": sum(1 for i in ids if not i),
        "duplicate_email_ids": sum(count - 1 for count in id_counts.values() if count > 1),
        "fully_unlabeled_rows": sum(
            1 for row in rows
            if not any((row.get(config["truth"]) or "").strip() for config in TASKS.values())
        ),
    }
    missing_truth_columns = [c["truth"] for c in TASKS.values() if c["truth"] not in headers]
    integrity["missing_truth_columns"] = missing_truth_columns
    integrity["priority_spam_conflicts"] = sum(
        1 for row in rows
        if _norm(row.get("true_priority"), "priority") == "SPAM"
        and _norm(row.get("true_is_spam"), "is_spam") == "no"
    )
    integrity["legacy_spam_in_true_priority"] = sum(
        1 for row in rows if _norm(row.get("true_priority"), "priority") == "SPAM"
    )

    adjudicated_ids: set[str] = set()
    adjudication_status = "not_provided"
    if adjudication_path is not None and adjudication_path.is_file():
        try:
            _, adjudication_rows = _read(adjudication_path)
            adjudicated_ids = {
                (row.get("email_id") or "").strip() for row in adjudication_rows
            } - {""}
            adjudication_status = "read"
        except AuditError:
            adjudication_status = "unreadable"

    prediction_sources = Counter(
        (row.get("label_source") or "").strip() or "(blank)" for row in rows
    )

    tasks: dict[str, Any] = {}
    for task, config in TASKS.items():
        labels = config["labels"]
        valid = {_truth_key(label, task) for label in labels}
        verified: list[tuple[str, str]] = []
        invalid = 0
        for row in rows:
            truth = _norm(row.get(config["truth"]), task)
            if not truth:
                continue
            if task == "priority" and truth == "SPAM":
                continue  # legacy value; evaluated by the is_spam task
            if truth not in valid:
                invalid += 1
                continue
            verified.append((row_key(row), truth))

        counts = Counter(label for _, label in verified)
        distribution = {_truth_key(label, task): counts.get(_truth_key(label, task), 0) for label in labels}
        present = [n for n in distribution.values() if n > 0]
        needed = {
            label: max(0, target_per_class - count)
            for label, count in distribution.items()
            if count < target_per_class
        }
        entry: dict[str, Any] = {
            "verified_labels": len(verified),
            "invalid_truth_values": invalid,
            "class_distribution": distribution,
            "classes_missing": [l for l, n in distribution.items() if n == 0],
            "classes_below_minimum": [l for l, n in distribution.items() if 0 < n < min_per_class],
            "imbalance_ratio_max_to_min": (
                round(max(present) / min(present), 2) if len(present) >= 2 else None
            ),
            "labels_needed_to_reach_target": needed,
            "target_per_class": target_per_class,
            "minimum_per_class": min_per_class,
            "from_final_adjudication": sum(1 for key, _ in verified if key in adjudicated_ids),
        }
        if manifest_path is not None:
            try:
                frozen = read_frozen_test_ids(manifest_path, task)
            except FeatureEvaluationError:
                frozen = None
                entry["split"] = "manifest_unreadable"
            else:
                if frozen is None:
                    entry["split"] = "no_frozen_split"
                else:
                    test_labels = Counter(label for key, label in verified if key in frozen)
                    entry["split"] = {
                        "development": sum(1 for key, _ in verified if key not in frozen),
                        "frozen_test": sum(1 for key, _ in verified if key in frozen),
                        "frozen_test_distribution": dict(sorted(test_labels.items())),
                    }
        tasks[task] = entry

    return {
        "canonical_dataset": {
            "file_name": input_path.name,
            "sha256_prefix": _file_fingerprint(input_path),
            "columns": len(headers),
        },
        "adjudication_file": adjudication_status,
        "integrity": integrity,
        "prediction_source_distribution": dict(sorted(prediction_sources.items())),
        "tasks": tasks,
        "privacy_note": "Aggregate counts only: no IDs, senders, subjects, or bodies.",
    }


def render_dataset_card(audit: dict[str, Any]) -> str:
    """Markdown dataset card (safe to commit: aggregate counts only)."""
    canonical = audit["canonical_dataset"]
    integrity = audit["integrity"]
    lines = [
        "# FRIDAY priority-engine dataset card",
        "",
        "Generated by `python -m scripts.audit_priority_dataset --write-doc ...`. "
        "Aggregate counts only; no email content, senders, or identifiers.",
        "",
        "## Canonical dataset",
        "",
        f"- **File:** `data/{canonical['file_name']}` (private, git-ignored; fingerprint `{canonical['sha256_prefix']}`)",
        "- **Built by:** `scripts.prepare_priority_engine_dataset` from `data/email_labels.csv` "
        "plus the final human adjudications. It is the only file used for reported results.",
        "- **Everything else** in `data/` (backups, checkpoints, relabel and adjudication working files) "
        "is provenance material, never evaluation input.",
        "- **Ground truth:** only non-empty, valid `true_*` columns written by a person. "
        "`predicted_*`, `rules_*` and `llm_*` columns are model output and are never ground truth.",
        "",
        "## Label provenance",
        "",
        f"- Rows: {integrity['rows']}  |  fully unlabeled rows: {integrity['fully_unlabeled_rows']}",
        "- Labels were entered by one annotator using `scripts.label_emails`, re-checked with a blind relabel "
        "(`scripts.check_label_agreement`), and disagreements were resolved by explicit adjudication.",
        f"- Final adjudication file: {audit['adjudication_file']}",
        f"- Prediction-source column (`label_source`, describes model output, not truth): {audit['prediction_source_distribution']}",
        "",
        "## Integrity checks",
        "",
        f"- Missing email IDs: {integrity['missing_email_ids']}  |  duplicate IDs: {integrity['duplicate_email_ids']}",
        f"- Missing truth columns: {integrity['missing_truth_columns'] or 'none'}",
        f"- Legacy SPAM values in `true_priority`: {integrity['legacy_spam_in_true_priority']} "
        f"(scored by the separate `is_spam` task); priority/spam conflicts: {integrity['priority_spam_conflicts']}",
        "",
        "## Verified labels per task",
        "",
    ]
    for task, entry in audit["tasks"].items():
        lines += [f"### {task}", ""]
        lines.append(f"- Verified labels: **{entry['verified_labels']}** "
                     f"(from final adjudication: {entry['from_final_adjudication']}; invalid values: {entry['invalid_truth_values']})")
        lines.append(f"- Class distribution: {entry['class_distribution']}")
        ratio = entry["imbalance_ratio_max_to_min"]
        lines.append(f"- Imbalance ratio (largest/smallest present class): {ratio if ratio is not None else 'n/a'}")
        if entry["classes_missing"]:
            lines.append(f"- Classes with **no** verified examples: {entry['classes_missing']}")
        if entry["classes_below_minimum"]:
            lines.append(f"- Classes below {entry['minimum_per_class']} examples: {entry['classes_below_minimum']}")
        if entry["labels_needed_to_reach_target"]:
            lines.append(f"- Labels still needed to reach {entry['target_per_class']} per class: "
                         f"{entry['labels_needed_to_reach_target']}")
        split = entry.get("split")
        if isinstance(split, dict):
            lines.append(f"- Split: development {split['development']}, frozen test {split['frozen_test']} "
                         f"{split['frozen_test_distribution']}")
        elif split:
            lines.append(f"- Split: {split}")
        lines.append("")
    lines += [
        "## Class-imbalance handling",
        "",
        "- Models use `class_weight=\"balanced\"` (see `backend/services/priority_model.py`).",
        "- Reports use macro-F1 and per-class recall, never accuracy alone.",
        "- `python -m scripts.suggest_labeling_batch` proposes unlabeled emails that are likely to belong to the "
        "under-represented classes so new labeling effort goes where it is needed. Candidates are heuristic "
        "suggestions only; labels must still be assigned by a person.",
        "",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--adjudication", type=Path, default=DEFAULT_ADJUDICATION)
    parser.add_argument("--output-json", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--write-doc", type=Path, help="Also write a committable dataset card (Markdown).")
    parser.add_argument("--min-per-class", type=int, default=DEFAULT_MIN_PER_CLASS)
    parser.add_argument("--target-per-class", type=int, default=DEFAULT_TARGET_PER_CLASS)
    args = parser.parse_args(argv)

    try:
        audit = audit_dataset(
            args.input, manifest_path=args.manifest, adjudication_path=args.adjudication,
            min_per_class=args.min_per_class, target_per_class=args.target_per_class,
        )
    except AuditError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(audit, indent=2), encoding="utf-8")
    print(f"Wrote {args.output_json} (aggregate counts only).")
    if args.write_doc:
        args.write_doc.parent.mkdir(parents=True, exist_ok=True)
        args.write_doc.write_text(render_dataset_card(audit), encoding="utf-8")
        print(f"Wrote {args.write_doc}.")
    for task, entry in audit["tasks"].items():
        print(f"- {task}: {entry['verified_labels']} verified; distribution {entry['class_distribution']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
