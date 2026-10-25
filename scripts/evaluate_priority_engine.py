"""Evaluate FRIDAY classifiers against human-verified labels only.

Uses a private persistent split manifest (default: data/priority_engine_split.json).
The frozen test IDs are never silently expanded: newly labeled emails are development
rows. Reports contain aggregate metrics only, never message content or raw IDs.
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

from scripts.priority_engine_splits import load_or_create_test_ids, row_key

TASKS = {
    "priority": {"truth": "true_priority", "rules": "rules_priority", "selected": "predicted_priority", "ollama": "llm_priority", "labels": ("URGENT", "NEEDS_REPLY", "INFORMATIONAL", "SPAM")},
    "needs_response": {"truth": "true_needs_response", "rules": "rules_needs_response", "selected": "predicted_needs_response", "ollama": "llm_needs_response", "labels": ("yes", "no")},
    "category": {"truth": "true_category", "rules": "rules_category", "selected": "predicted_category", "ollama": "llm_category", "labels": ("JOB", "FINANCE", "PERSONAL", "PROMOTIONS", "UPDATES", "OTHER")},
}
INVALID = "__INVALID_OR_MISSING__"


def _norm(value: str | None, task: str) -> str:
    value = (value or "").strip()
    return value.lower() if task == "needs_response" else value.upper()


def metrics(y_true: list[str], y_pred: list[str], labels: tuple[str, ...]) -> dict[str, Any]:
    """Metrics count invalid/missing predictions as errors instead of dropping them."""
    if len(y_true) != len(y_pred):
        raise ValueError("y_true and y_pred must have equal lengths")
    n = len(y_true)
    allowed = set(labels)
    normalized_predictions = [prediction if prediction in allowed else INVALID for prediction in y_pred]
    per_class: dict[str, Any] = {}
    matrix = {actual: {predicted: 0 for predicted in (*labels, INVALID)} for actual in labels}
    for actual, predicted in zip(y_true, normalized_predictions):
        if actual in matrix:
            matrix[actual][predicted] += 1
    for label in labels:
        tp = sum(a == label and p == label for a, p in zip(y_true, normalized_predictions))
        fp = sum(a != label and p == label for a, p in zip(y_true, normalized_predictions))
        fn = sum(a == label and p != label for a, p in zip(y_true, normalized_predictions))
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        per_class[label] = {"support": sum(a == label for a in y_true), "precision": round(precision, 4), "recall": round(recall, 4), "f1": round(f1, 4)}
    invalid_count = sum(prediction == INVALID for prediction in normalized_predictions)
    return {
        "samples": n,
        "prediction_coverage": round((n - invalid_count) / n, 4) if n else None,
        "invalid_or_missing_predictions": invalid_count,
        "accuracy": round(sum(a == p for a, p in zip(y_true, normalized_predictions)) / n, 4) if n else None,
        "macro_f1": round(sum(item["f1"] for item in per_class.values()) / len(labels), 4) if labels else None,
        "per_class": per_class,
        "confusion_matrix_rows_actual_columns_predicted": matrix,
    }


def _features(row: dict[str, str]) -> str:
    labels = (row.get("labels") or "").replace("|", " ")
    return " ".join((row.get("sender") or "", row.get("subject") or "", row.get("snippet") or "", row.get("body_text") or "", labels))


def evaluate(input_path: Path, *, test_fraction: float = 0.25, seed: int = 42,
             min_verified: int = 30, split_manifest: Path | None = None) -> dict[str, Any]:
    with input_path.open("r", newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    manifest_path = split_manifest or input_path.parent / "priority_engine_split.json"
    report: dict[str, Any] = {
        "input_file_name": input_path.name, "seed_requested": seed, "test_fraction_requested": test_fraction,
        "split_manifest_file_name": manifest_path.name,
        "ground_truth_policy": "Only non-empty valid true_* labels are used. Predictions are never ground truth.",
        "privacy_note": "No sender, subject, body, snippets, or raw email IDs are written to this report. The split manifest is private and must remain git-ignored.",
        "test_set_policy": "Test IDs are persisted in a private manifest. Newly verified IDs absent from the manifest are development-only.",
        "tasks": {},
    }

    for task, config in TASKS.items():
        labels = config["labels"]
        valid = {label.lower() if task == "needs_response" else label for label in labels}
        verified: list[dict[str, str]] = []
        invalid_truth = 0
        for source in rows:
            truth = _norm(source.get(config["truth"]), task)
            if not truth:
                continue
            if truth not in valid:
                invalid_truth += 1
                continue
            clean = dict(source)
            clean["_truth"] = truth
            clean["_row_key"] = row_key(source)
            verified.append(clean)
        distribution = dict(Counter(row["_truth"] for row in verified))
        result: dict[str, Any] = {"verified_labels": len(verified), "invalid_truth_rows": invalid_truth, "class_distribution": distribution}
        represented = [label for label in labels if distribution.get(label.lower() if task == "needs_response" else label, 0)]
        if len(verified) < max(min_verified, 2 * len(represented)) or len(represented) < 2 or any(distribution.get(label.lower() if task == "needs_response" else label, 0) < 2 for label in represented):
            result.update({"status": "insufficient_verified_data", "message": "Collect more human-verified examples, including at least two for each represented class, before comparing models."})
            report["tasks"][task] = result
            continue

        try:
            from sklearn.feature_extraction.text import TfidfVectorizer
            from sklearn.linear_model import LogisticRegression
            from sklearn.pipeline import Pipeline
            test_ids, manifest_created = load_or_create_test_ids(
                rows, task=task, truth_column=config["truth"], labels=labels,
                manifest_path=manifest_path, test_fraction=test_fraction, seed=seed,
            )
        except ImportError:
            result.update({"status": "missing_optional_dependency", "message": "Install ML dependencies with: pip install -r requirements-ml.txt"})
            report["tasks"][task] = result
            continue
        except ValueError as exc:
            result.update({"status": "invalid_split_manifest", "message": str(exc)})
            report["tasks"][task] = result
            continue

        test_rows = [row for row in verified if row["_row_key"] in test_ids]
        train_rows = [row for row in verified if row["_row_key"] not in test_ids]
        if not test_rows or not train_rows:
            result.update({"status": "unable_to_split", "message": "Frozen test manifest does not leave both development and test rows. Review the private manifest before changing it."})
            report["tasks"][task] = result
            continue
        y_true = [row["_truth"] for row in test_rows]
        normalized_labels = tuple(label.lower() for label in labels) if task == "needs_response" else labels

        def score_column(column: str) -> dict[str, Any]:
            predictions = [_norm(row.get(column), task) for row in test_rows]
            return metrics(y_true, predictions, normalized_labels)

        model = Pipeline([
            ("tfidf", TfidfVectorizer(ngram_range=(1, 2), min_df=1, max_features=30000, sublinear_tf=True)),
            ("classifier", LogisticRegression(max_iter=2000, class_weight="balanced", random_state=seed)),
        ])
        try:
            model.fit([_features(row) for row in train_rows], [row["_truth"] for row in train_rows])
            ml_predictions = model.predict([_features(row) for row in test_rows]).tolist()
        except ValueError as exc:
            result.update({"status": "model_training_failed", "message": str(exc)})
            report["tasks"][task] = result
            continue
        result.update({
            "status": "holdout_evaluation" if len(verified) >= min_verified else "exploratory",
            "development_rows": len(train_rows), "frozen_test_rows": len(test_rows),
            "frozen_test_manifest_created_this_run": manifest_created,
            "rules": score_column(config["rules"]),
            "stored_selected_predictions": score_column(config["selected"]),
            "stored_ollama_predictions": score_column(config["ollama"]),
            "tfidf_logistic_regression": metrics(y_true, [_norm(value, task) for value in ml_predictions], normalized_labels),
            "note": "The test IDs are frozen in a private manifest. Do not tune against this holdout. Small samples have high uncertainty.",
        })
        report["tasks"][task] = result
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("data/email_labels.csv"))
    parser.add_argument("--output", type=Path, default=Path("reports/priority_engine_evaluation.json"))
    parser.add_argument("--split-manifest", type=Path, default=Path("data/priority_engine_split.json"))
    parser.add_argument("--test-fraction", type=float, default=0.25)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--min-verified", type=int, default=30)
    args = parser.parse_args()
    if not 0.1 <= args.test_fraction <= 0.4:
        parser.error("--test-fraction must be between 0.1 and 0.4")
    if args.min_verified < 2:
        parser.error("--min-verified must be at least 2")
    if not args.input.is_file():
        parser.error(f"Input CSV not found: {args.input}")
    try:
        report = evaluate(args.input, test_fraction=args.test_fraction, seed=args.seed, min_verified=args.min_verified, split_manifest=args.split_manifest)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    md = ["# FRIDAY Priority Engine Evaluation", "", f"- Dataset file: `{args.input.name}`", f"- Split manifest: `{args.split_manifest.name}` (private; do not commit)", f"- Requested seed: {args.seed}", f"- Requested test fraction: {args.test_fraction}", "- Ground truth: human-verified `true_*` columns only.", "- Invalid/missing predictions count as errors and reduce prediction coverage.", "- Reports contain aggregate metrics only; no raw email content or message identifiers.", ""]
    for task, result in report["tasks"].items():
        md.extend([f"## {task}", f"- Verified labels: {result['verified_labels']}", f"- Status: {result['status']}"])
        if "message" in result:
            md.append(f"- Note: {result['message']}")
        if "rules" in result:
            for model_name in ("rules", "stored_selected_predictions", "stored_ollama_predictions", "tfidf_logistic_regression"):
                score = result.get(model_name, {})
                md.append(f"- {model_name}: macro-F1 `{score.get('macro_f1', 'n/a')}`, accuracy `{score.get('accuracy', 'n/a')}`, coverage `{score.get('prediction_coverage', 'n/a')}`")
        md.append("")
    args.output.with_suffix(".md").write_text("\n".join(md), encoding="utf-8")
    print(json.dumps({key: {"verified_labels": value["verified_labels"], "status": value["status"]} for key, value in report["tasks"].items()}, indent=2))
    print(f"Reports written to {args.output} and {args.output.with_suffix('.md')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
