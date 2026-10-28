"""Compare spam-classification thresholds using development-only data.

Uses out-of-fold probabilities and excludes frozen holdout IDs.
The frozen split manifest is read-only. Reports contain aggregate metrics only.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any, Sequence

from scripts.evaluate_priority_engine import _features, _norm
from scripts.evaluate_priority_engine_development import _read_manifest
from scripts.priority_engine_splits import row_key


DEFAULT_THRESHOLDS = (0.30, 0.40, 0.50, 0.60, 0.70)


def evaluate_thresholds(
    y_true: Sequence[str],
    probabilities: Sequence[float],
    *,
    thresholds: Sequence[float],
) -> dict[str, Any]:
    """Evaluate classification metrics and error counts at each threshold."""
    if len(y_true) != len(probabilities):
        raise ValueError(
            "Ground-truth labels and probabilities must have equal lengths."
        )

    if not thresholds:
        raise ValueError("At least one threshold is required.")

    if any(
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not 0.0 <= value <= 1.0
        for value in thresholds
    ):
        raise ValueError("Every threshold must be between 0 and 1.")

    truth = [str(label).strip().lower() for label in y_true]

    if any(label not in {"yes", "no"} for label in truth):
        raise ValueError("Ground-truth labels must be 'yes' or 'no'.")

    if any(
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not 0.0 <= value <= 1.0
        for value in probabilities
    ):
        raise ValueError("Probabilities must be between 0 and 1.")

    probs = [float(value) for value in probabilities]
    spam_count = truth.count("yes")
    legitimate_count = truth.count("no")

    results: list[dict[str, Any]] = []
    predictions_by_threshold: dict[float, list[str]] = {}

    for threshold_value in thresholds:
        threshold = float(threshold_value)
        predictions = [
            "yes" if probability >= threshold else "no"
            for probability in probs
        ]
        predictions_by_threshold[threshold] = predictions

        tp = sum(actual == "yes" and pred == "yes"
                 for actual, pred in zip(truth, predictions))
        fp = sum(actual == "no" and pred == "yes"
                 for actual, pred in zip(truth, predictions))
        fn = sum(actual == "yes" and pred == "no"
                 for actual, pred in zip(truth, predictions))
        tn = sum(actual == "no" and pred == "no"
                 for actual, pred in zip(truth, predictions))

        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / spam_count if spam_count else 0.0
        specificity = tn / legitimate_count if legitimate_count else 0.0

        spam_f1 = (
            2 * precision * recall / (precision + recall)
            if precision + recall else 0.0
        )

        legitimate_precision = (
            tn / (tn + fn) if tn + fn else 0.0
        )
        legitimate_recall = specificity

        legitimate_f1 = (
            2 * legitimate_precision * legitimate_recall
            / (legitimate_precision + legitimate_recall)
            if legitimate_precision + legitimate_recall else 0.0
        )

        results.append({
            "threshold": threshold,
            "spam_precision": round(precision, 4),
            "spam_recall": round(recall, 4),
            "false_positive_rate": round(
                fp / legitimate_count if legitimate_count else 0.0, 4
            ),
            "specificity": round(specificity, 4),
            "spam_f1": round(spam_f1, 4),
            "legitimate_f1": round(legitimate_f1, 4),
            "macro_f1": round((spam_f1 + legitimate_f1) / 2, 4),
            "true_positive": tp,
            "false_positive": fp,
            "false_negative": fn,
            "true_negative": tn,
        })

    # Compare each threshold with the previous threshold in the supplied order.
    transitions: list[dict[str, Any]] = []

    for previous_threshold, current_threshold in zip(
        list(predictions_by_threshold)[:-1],
        list(predictions_by_threshold)[1:],
    ):
        previous = predictions_by_threshold[previous_threshold]
        current = predictions_by_threshold[current_threshold]

        transitions.append({
            "from_threshold": previous_threshold,
            "to_threshold": current_threshold,
            "changed_predictions": sum(
                left != right for left, right in zip(previous, current)
            ),
            "spam_predictions_before": previous.count("yes"),
            "spam_predictions_after": current.count("yes"),
            "false_positives_before": sum(
                actual == "no" and pred == "yes"
                for actual, pred in zip(truth, previous)
            ),
            "false_positives_after": sum(
                actual == "no" and pred == "yes"
                for actual, pred in zip(truth, current)
            ),
            "false_negatives_before": sum(
                actual == "yes" and pred == "no"
                for actual, pred in zip(truth, previous)
            ),
            "false_negatives_after": sum(
                actual == "yes" and pred == "no"
                for actual, pred in zip(truth, current)
            ),
        })

    return {
        "samples": len(truth),
        "spam_samples": spam_count,
        "legitimate_samples": legitimate_count,
        "threshold_results": results,
        "threshold_transitions": transitions,
    }


def diagnose_thresholds(
    input_path: Path,
    manifest_path: Path,
    *,
    seed: int = 42,
    max_folds: int = 5,
    thresholds: Sequence[float] = DEFAULT_THRESHOLDS,
) -> dict[str, Any]:
    """Evaluate word-based spam probabilities using development CV only."""
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.linear_model import LogisticRegression
        from sklearn.model_selection import StratifiedKFold, cross_val_predict
        from sklearn.pipeline import Pipeline
    except ImportError as exc:
        raise ValueError(
            "Install ML dependencies: pip install -r requirements-ml.txt"
        ) from exc

    if max_folds < 2:
        raise ValueError("max_folds must be at least 2.")

    with input_path.open(
        "r", newline="", encoding="utf-8-sig"
    ) as handle:
        source_rows = list(csv.DictReader(handle))

    frozen = _read_manifest(manifest_path)

    if "is_spam" not in frozen:
        raise ValueError("The frozen manifest has no is_spam task.")

    keys = [row_key(row) for row in source_rows]

    if len(keys) != len(set(keys)):
        raise ValueError("Source CSV contains duplicate row keys.")

    dev_rows: list[dict[str, str]] = []

    for row in source_rows:
        truth = _norm(row.get("true_is_spam"), "is_spam")

        if truth not in {"yes", "no"}:
            continue

        if row_key(row) in frozen["is_spam"]:
            continue

        dev_rows.append(row)

    y_true = [
        _norm(row.get("true_is_spam"), "is_spam")
        for row in dev_rows
    ]

    spam_count = y_true.count("yes")
    legitimate_count = y_true.count("no")

    if spam_count < 2 or legitimate_count < 2:
        raise ValueError(
            "Need at least two development examples per class."
        )

    folds = min(max_folds, spam_count, legitimate_count)

    model = Pipeline([
        (
            "tfidf",
            TfidfVectorizer(
                analyzer="word",
                ngram_range=(1, 2),
                min_df=1,
                max_features=30000,
                sublinear_tf=True,
            ),
        ),
        (
            "classifier",
            LogisticRegression(
                max_iter=2000,
                class_weight="balanced",
                random_state=seed,
            ),
        ),
    ])

    cv = StratifiedKFold(
        n_splits=folds,
        shuffle=True,
        random_state=seed,
    )

    probabilities = cross_val_predict(
        model,
        [_features(row) for row in dev_rows],
        y_true,
        cv=cv,
        method="predict_proba",
    )

    # Fit only on development rows to determine the class-column ordering.
    model.fit([_features(row) for row in dev_rows], y_true)
    classes = list(model.named_steps["classifier"].classes_)

    if "yes" not in classes:
        raise ValueError("The spam class 'yes' is missing from model classes.")

    spam_column = classes.index("yes")
    spam_probabilities = probabilities[:, spam_column].tolist()

    comparison = evaluate_thresholds(
        y_true,
        spam_probabilities,
        thresholds=thresholds,
    )

    return {
        "evaluation_method": (
            "Stratified out-of-fold probabilities on development data only"
        ),
        "holdout_policy": (
            "Frozen test IDs are excluded before cross-validation. "
            "The manifest is never modified."
        ),
        "privacy_note": (
            "Aggregate metrics only. No email content or message identifiers "
            "are included in the report."
        ),
        "development_rows": len(dev_rows),
        "frozen_holdout_rows_excluded": sum(
            row_key(row) in frozen["is_spam"] for row in source_rows
        ),
        "folds": folds,
        "model": "word_tfidf_logistic_regression",
        **comparison,
        "note": (
            "Thresholds are exploratory development comparisons. "
            "Do not tune against the frozen holdout."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input", type=Path, default=Path("data/email_labels.csv")
    )
    parser.add_argument(
        "--split-manifest",
        type=Path,
        default=Path("data/priority_engine_split.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/spam_threshold_experiment.json"),
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--max-folds", type=int, default=5)

    args = parser.parse_args()

    if not args.input.is_file():
        parser.error(f"Input CSV not found: {args.input}")

    if not args.split_manifest.is_file():
        parser.error(
            f"Frozen split manifest not found: {args.split_manifest}"
        )

    try:
        report = diagnose_thresholds(
            args.input,
            args.split_manifest,
            seed=args.seed,
            max_folds=args.max_folds,
        )
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2),
        encoding="utf-8",
    )

    print(json.dumps({
        "development_rows": report["development_rows"],
        "frozen_holdout_rows_excluded": (
            report["frozen_holdout_rows_excluded"]
        ),
        "threshold_results": report["threshold_results"],
        "threshold_transitions": report["threshold_transitions"],
        "output_file": str(args.output),
    }, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())