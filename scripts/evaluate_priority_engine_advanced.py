"""Leakage-aware FRIDAY classifier evaluation: development CV + frozen holdout.

Only human-verified true_* columns are ground truth. The persistent split manifest
is private. Reports contain aggregate metrics only; never include message IDs/text.
"""
from __future__ import annotations

import argparse
import csv
import json
import random
from pathlib import Path
from typing import Any

from scripts.evaluate_priority_engine import TASKS, INVALID, _features, _norm, metrics
from scripts.priority_engine_splits import load_or_create_test_ids, row_key


def bootstrap_intervals(
    y_true: list[str], y_pred: list[str], labels: tuple[str, ...], *,
    iterations: int = 1000, seed: int = 42,
) -> dict[str, dict[str, float] | None]:
    """Percentile bootstrap CIs over paired holdout predictions."""
    if len(y_true) != len(y_pred):
        raise ValueError("y_true and y_pred must have equal lengths")
    if not y_true:
        return {"accuracy_95ci": None, "macro_f1_95ci": None}
    rng = random.Random(seed)
    accuracy_values: list[float] = []
    f1_values: list[float] = []
    n = len(y_true)
    for _ in range(iterations):
        indices = [rng.randrange(n) for _ in range(n)]
        sample = metrics(
            [y_true[i] for i in indices],
            [y_pred[i] for i in indices],
            labels,
        )
        accuracy_values.append(float(sample["accuracy"] or 0.0))
        f1_values.append(float(sample["macro_f1"] or 0.0))

    def interval(values: list[float]) -> dict[str, float]:
        values.sort()
        low = values[max(0, int(0.025 * (len(values) - 1)))]
        high = values[min(len(values) - 1, int(0.975 * (len(values) - 1)))]
        return {"lower": round(low, 4), "upper": round(high, 4)}

    return {"accuracy_95ci": interval(accuracy_values), "macro_f1_95ci": interval(f1_values)}


def evaluate_advanced(
    input_path: Path, *, split_manifest: Path | None = None, seed: int = 42,
    test_fraction: float = 0.25, min_verified: int = 30, bootstrap_iterations: int = 1000,
) -> dict[str, Any]:
    with input_path.open("r", newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    manifest_path = split_manifest or input_path.parent / "priority_engine_split.json"
    report: dict[str, Any] = {
        "input_file_name": input_path.name,
        "split_manifest_file_name": manifest_path.name,
        "ground_truth_policy": "Only valid, non-empty human-verified true_* labels are ground truth.",
        "privacy_note": "Aggregate metrics only; no email content or raw identifiers.",
        "methodology": {
            "development": "Stratified cross-validation where class counts permit; folds capped by smallest class.",
            "test": "Persistent frozen test IDs from private manifest; never used for model selection.",
            "confidence_intervals": f"Percentile bootstrap, {bootstrap_iterations} resamples; small holdouts have wide uncertainty.",
        },
        "tasks": {},
    }

    for task, config in TASKS.items():
        labels = tuple(label.lower() for label in config["labels"]) if task == "needs_response" else config["labels"]
        valid = set(labels)
        verified: list[dict[str, str]] = []
        invalid_truth = 0
        for source in rows:
            truth = _norm(source.get(config["truth"]), task)
            if not truth:
                continue
            if truth not in valid:
                invalid_truth += 1
                continue
            row = dict(source)
            row["_truth"] = truth
            row["_row_key"] = row_key(source)
            verified.append(row)

        result: dict[str, Any] = {
            "verified_labels": len(verified),
            "invalid_truth_rows": invalid_truth,
        }
        counts = {label: sum(row["_truth"] == label for row in verified) for label in labels}
        result["class_distribution"] = counts
        represented = [label for label, count in counts.items() if count]
        if len(verified) < min_verified or len(represented) < 2 or min(counts[label] for label in represented) < 2:
            result.update({
                "status": "insufficient_verified_data",
                "message": "Collect more verified examples across classes before drawing model-quality conclusions.",
            })
            report["tasks"][task] = result
            continue

        try:
            from sklearn.feature_extraction.text import TfidfVectorizer
            from sklearn.linear_model import LogisticRegression
            from sklearn.model_selection import StratifiedKFold, cross_validate
            from sklearn.pipeline import Pipeline
        except ImportError:
            result.update({"status": "missing_optional_dependency", "message": "Install requirements-ml.txt"})
            report["tasks"][task] = result
            continue

        try:
            test_ids, manifest_created = load_or_create_test_ids(
                rows, task=task, truth_column=config["truth"], labels=config["labels"],
                manifest_path=manifest_path, test_fraction=test_fraction, seed=seed,
            )
        except (OSError, ValueError) as exc:
            result.update({"status": "invalid_split_manifest", "message": str(exc)})
            report["tasks"][task] = result
            continue

        test_rows = [row for row in verified if row["_row_key"] in test_ids]
        dev_rows = [row for row in verified if row["_row_key"] not in test_ids]
        if not test_rows or not dev_rows:
            result.update({"status": "unable_to_split", "message": "Frozen manifest must leave both development and test rows."})
            report["tasks"][task] = result
            continue
        dev_counts = [sum(row["_truth"] == label for row in dev_rows) for label in set(row["_truth"] for row in dev_rows)]
        folds = min(5, min(dev_counts)) if dev_counts else 0
        model = Pipeline([
            ("tfidf", TfidfVectorizer(ngram_range=(1, 2), min_df=1, max_features=30000, sublinear_tf=True)),
            ("classifier", LogisticRegression(max_iter=2000, class_weight="balanced", random_state=seed)),
        ])
        cv_summary: dict[str, Any]
        if folds >= 2:
            cv = StratifiedKFold(n_splits=folds, shuffle=True, random_state=seed)
            cv_result = cross_validate(
                model, [_features(row) for row in dev_rows], [row["_truth"] for row in dev_rows],
                cv=cv, scoring={"accuracy": "accuracy", "macro_f1": "f1_macro"},
                error_score="raise",
            )
            cv_summary = {
                "folds": folds,
                "accuracy_mean": round(float(cv_result["test_accuracy"].mean()), 4),
                "accuracy_std": round(float(cv_result["test_accuracy"].std()), 4),
                "macro_f1_mean": round(float(cv_result["test_macro_f1"].mean()), 4),
                "macro_f1_std": round(float(cv_result["test_macro_f1"].std()), 4),
            }
        else:
            cv_summary = {"folds": 0, "status": "insufficient_per_class_development_rows"}

        model.fit([_features(row) for row in dev_rows], [row["_truth"] for row in dev_rows])
        y_true = [row["_truth"] for row in test_rows]
        ml_predictions = [_norm(value, task) for value in model.predict([_features(row) for row in test_rows]).tolist()]
        rules_predictions = [_norm(row.get(config["rules"]), task) for row in test_rows]
        selected_predictions = [_norm(row.get(config["selected"]), task) for row in test_rows]
        ollama_column = config.get("ollama")
        ollama_predictions = [_norm(row.get(ollama_column), task) for row in test_rows] if ollama_column else [""] * len(test_rows)
        result.update({
            "status": "holdout_evaluation",
            "development_rows": len(dev_rows),
            "frozen_test_rows": len(test_rows),
            "frozen_test_manifest_created_this_run": manifest_created,
            "development_cross_validation": cv_summary,
            "frozen_test": {
                "rules": metrics(y_true, rules_predictions, labels),
                "stored_selected_predictions": metrics(y_true, selected_predictions, labels),
                "stored_ollama_predictions": metrics(y_true, ollama_predictions, labels),
                "tfidf_logistic_regression": metrics(y_true, ml_predictions, labels),
            },
            "bootstrap_95_percent_intervals": {
                "rules": bootstrap_intervals(y_true, rules_predictions, labels, iterations=bootstrap_iterations, seed=seed),
                "stored_selected_predictions": bootstrap_intervals(y_true, selected_predictions, labels, iterations=bootstrap_iterations, seed=seed),
                "stored_ollama_predictions": bootstrap_intervals(y_true, ollama_predictions, labels, iterations=bootstrap_iterations, seed=seed),
                "tfidf_logistic_regression": bootstrap_intervals(y_true, ml_predictions, labels, iterations=bootstrap_iterations, seed=seed),
            },
            "note": "Do not tune against the frozen holdout. Metrics are estimates; small test sets have high uncertainty.",
        })
        report["tasks"][task] = result
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("data/email_labels.csv"))
    parser.add_argument("--output", type=Path, default=Path("reports/priority_engine_advanced_evaluation.json"))
    parser.add_argument("--split-manifest", type=Path, default=Path("data/priority_engine_split.json"))
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--test-fraction", type=float, default=0.25)
    parser.add_argument("--min-verified", type=int, default=30)
    parser.add_argument("--bootstrap-iterations", type=int, default=1000)
    args = parser.parse_args()
    if not args.input.is_file():
        parser.error(f"Input CSV not found: {args.input}")
    if not 0.1 <= args.test_fraction <= 0.4:
        parser.error("--test-fraction must be between 0.1 and 0.4")
    if args.bootstrap_iterations < 100:
        parser.error("--bootstrap-iterations must be at least 100")
    report = evaluate_advanced(
        args.input, split_manifest=args.split_manifest, seed=args.seed,
        test_fraction=args.test_fraction, min_verified=args.min_verified,
        bootstrap_iterations=args.bootstrap_iterations,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    lines = [
        "# FRIDAY Advanced Priority Engine Evaluation", "",
        f"- Dataset: `{args.input.name}`",
        f"- Private split manifest: `{args.split_manifest.name}`",
        "- Ground truth: human-verified `true_*` fields only.",
        "- Test IDs remain frozen; use development CV for model selection.",
        "- Reports contain aggregate metrics only.", "",
    ]
    for task, item in report["tasks"].items():
        lines.extend([f"## {task}", f"- Verified labels: {item['verified_labels']}", f"- Status: {item['status']}"])
        if "frozen_test" in item:
            for model_name, score in item["frozen_test"].items():
                ci = item["bootstrap_95_percent_intervals"][model_name]
                lines.append(f"- {model_name}: accuracy={score['accuracy']}, macro-F1={score['macro_f1']}, samples={score['samples']}, accuracy 95% CI={ci['accuracy_95ci']}")
            lines.append(f"- Development CV: `{item['development_cross_validation']}`")
        if "message" in item:
            lines.append(f"- Note: {item['message']}")
        lines.append("")
    args.output.with_suffix(".md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({task: {"verified_labels": value["verified_labels"], "status": value["status"]} for task, value in report["tasks"].items()}, indent=2))
    print(f"Reports written to {args.output} and {args.output.with_suffix('.md')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
