from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

from scripts.auto_label_emails import classify_with_rules
from scripts.evaluate_priority_engine import TASKS, _features, _norm, metrics
from scripts.priority_engine_splits import row_key

def _read_manifest(path: Path) -> dict[str, set[str]]:
    """Read and validate the frozen split manifest without modifying it."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"Cannot read frozen split manifest: {path}. "
            "This script will not create or repair it."
        ) from exc

    if (
        not isinstance(raw, dict)
        or raw.get("version") != 1
        or not isinstance(raw.get("tasks"), dict)
    ):
        raise ValueError(
            "Frozen split manifest has an unsupported schema."
        )

    result: dict[str, set[str]] = {}

    for task, entry in raw["tasks"].items():
        if not isinstance(entry, dict):
            raise ValueError(
                f"Malformed manifest entry for task {task!r}."
            )

        ids = entry.get("test_ids")

        if (
            not isinstance(ids, list)
            or any(
                not isinstance(item, str) or not item.strip()
                for item in ids
            )
        ):
            raise ValueError(
                f"Malformed frozen test IDs for task {task!r}."
            )

        if len(ids) != len(set(ids)):
            raise ValueError(
                f"Duplicate frozen test IDs for task {task!r}."
            )

        result[task] = set(ids)

    return result

def _spam_error_diagnostics(
    y_true: list[str],
    rules_predictions: list[str],
    word_predictions: list[str],
    char_predictions: list[str],
) -> dict[str, Any]:
    """Summarize spam classification errors without exposing individual rows."""
    if not (
        len(y_true)
        == len(rules_predictions)
        == len(word_predictions)
        == len(char_predictions)
    ):
        raise ValueError(
            "All prediction lists must have equal lengths."
        )

    result: dict[str, Any] = {}

    # Count false positives and false negatives for each classifier.
    for model_name, predictions in (
        ("rules", rules_predictions),
        ("word_tfidf", word_predictions),
        ("char_tfidf", char_predictions),
    ):
        false_positives = sum(
            truth == "no" and prediction == "yes"
            for truth, prediction in zip(y_true, predictions)
        )

        false_negatives = sum(
            truth == "yes" and prediction == "no"
            for truth, prediction in zip(y_true, predictions)
        )

        result[model_name] = {
            "false_positives": false_positives,
            "false_negatives": false_negatives,
            "total_errors": false_positives + false_negatives,
        }

    # Find examples where both ML classifiers are wrong.
    shared_errors = [
        index
        for index, (truth, word, char) in enumerate(
            zip(y_true, word_predictions, char_predictions)
        )
        if word != truth and char != truth
    ]

    result["word_char_shared_errors"] = {
        "total": len(shared_errors),
        "actual_spam_missed_by_both": sum(
            y_true[index] == "yes"
            for index in shared_errors
        ),
        "non_spam_flagged_by_both": sum(
            y_true[index] == "no"
            for index in shared_errors
        ),
        "rules_correct_on_shared_errors": sum(
            rules_predictions[index] == y_true[index]
            for index in shared_errors
        ),
        "rules_also_wrong_on_shared_errors": sum(
            rules_predictions[index] != y_true[index]
            for index in shared_errors
        ),
    }

    return result

def evaluate_development(
    input_path: Path,
    manifest_path: Path,
    *,
    seed: int = 42,
    max_folds: int = 5,
) -> dict[str, Any]:
    """Evaluate rules and TF-IDF candidates using development rows only."""
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.linear_model import LogisticRegression
        from sklearn.model_selection import (
            StratifiedKFold,
            cross_val_predict,
        )
        from sklearn.pipeline import Pipeline
    except ImportError as exc:
        raise ValueError(
            "Install optional ML dependencies: "
            "pip install -r requirements-ml.txt"
        ) from exc

    with input_path.open(
        "r",
        newline="",
        encoding="utf-8-sig",
    ) as handle:
        rows = list(csv.DictReader(handle))

    # Read the manifest only. Never create, repair, or rewrite it.
    frozen = _read_manifest(manifest_path)

    keys = [row_key(row) for row in rows]

    if len(keys) != len(set(keys)):
        raise ValueError(
            "Source CSV contains duplicate row keys; "
            "resolve before evaluation."
        )

    report: dict[str, Any] = {
        "input_file_name": input_path.name,
        "manifest_file_name": manifest_path.name,
        "evaluation_method": (
            "stratified cross-validation on development rows only"
        ),
        "seed": seed,
        "max_folds": max_folds,
        "holdout_policy": (
            "Frozen test IDs are excluded; "
            "this script never writes the manifest."
        ),
        "privacy_note": (
            "Aggregate metrics only; no email text or raw "
            "identifiers are emitted."
        ),
        "tasks": {},
    }

    for task, config in TASKS.items():
        if task not in frozen:
            report["tasks"][task] = {
                "status": "missing_frozen_task",
                "message": (
                    "Task has no frozen test assignment; "
                    "no evaluation was run."
                ),
            }
            continue

        labels = tuple(
            label.lower()
            if task in {"needs_response", "is_spam"}
            else label
            for label in config["labels"]
        )

        valid = set(labels)
        truth_col = config["truth"]

        dev: list[dict[str, str]] = []
        invalid_truth = 0
        excluded_holdout = 0

        for source in rows:
            truth = _norm(source.get(truth_col), task)

            # Spam emails are evaluated by the separate spam task,
            # not as priority labels.
            if task == "priority" and truth == "SPAM":
                continue

            if not truth:
                continue

            if truth not in valid:
                invalid_truth += 1
                continue

            # Never include frozen test rows in development evaluation.
            if row_key(source) in frozen[task]:
                excluded_holdout += 1
                continue

            clean = dict(source)
            clean["_truth"] = truth
            dev.append(clean)

        distribution = Counter(
            row["_truth"] for row in dev
        )

        result: dict[str, Any] = {
            "status": "development_cross_validation",
            "development_rows": len(dev),
            "frozen_holdout_rows_excluded": excluded_holdout,
            "invalid_truth_rows": invalid_truth,
            "class_distribution": dict(distribution),
        }

        represented = [
            label
            for label in labels
            if distribution.get(label, 0) > 0
        ]

        min_class_count = min(
            (
                distribution[label]
                for label in represented
            ),
            default=0,
        )

        folds = min(max_folds, min_class_count)

        if len(represented) < 2 or folds < 2:
            result.update(
                {
                    "status": "insufficient_development_data",
                    "message": (
                        "Need at least two represented classes "
                        "with at least two development examples each."
                    ),
                }
            )

            report["tasks"][task] = result
            continue

        def prediction_for_rules(
            row: dict[str, str],
        ) -> str:
            output = classify_with_rules(row)

            if task == "priority":
                return _norm(output.get("priority"), task)

            if task == "is_spam":
                return _norm(output.get("is_spam"), task)

            if task == "needs_response":
                return _norm(
                    output.get("needs_response"),
                    task,
                )

            return _norm(output.get("category"), task)

        y_true = [
            row["_truth"]
            for row in dev
        ]

        rules_pred = [
            prediction_for_rules(row)
            for row in dev
        ]

        cv = StratifiedKFold(
            n_splits=folds,
            shuffle=True,
            random_state=seed,
        )

        X = [
            _features(row)
            for row in dev
        ]

        candidates = {
            "word_tfidf_logistic_regression": Pipeline(
                [
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
                ]
            ),
            "char_tfidf_logistic_regression": Pipeline(
                [
                    (
                        "tfidf",
                        TfidfVectorizer(
                            analyzer="char_wb",
                            ngram_range=(3, 5),
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
                ]
            ),
        }

        result["folds"] = folds
        result["rules"] = metrics(
            y_true,
            rules_pred,
            labels,
        )
        result["candidates"] = {}

        # Predictions stay in memory for aggregate comparisons only.
        # Never write individual predictions or email identifiers.
        candidate_predictions: dict[str, list[str]] = {}

        for name, model in candidates.items():
            try:
                predictions = cross_val_predict(
                    model,
                    X,
                    y_true,
                    cv=cv,
                    method="predict",
                )

                prediction_list = predictions.tolist()
                candidate_predictions[name] = prediction_list

                result["candidates"][name] = metrics(
                    y_true,
                    prediction_list,
                    labels,
                )

            except (ValueError, TypeError) as exc:
                result["candidates"][name] = {
                    "status": "candidate_failed",
                    "message": (
                        f"{type(exc).__name__}: "
                        "candidate could not be evaluated."
                    ),
                }

        word_name = "word_tfidf_logistic_regression"
        char_name = "char_tfidf_logistic_regression"

        if (
            word_name in candidate_predictions
            and char_name in candidate_predictions
        ):
            word_predictions = candidate_predictions[word_name]
            char_predictions = candidate_predictions[char_name]

            if (
                len(word_predictions)
                == len(char_predictions)
                == len(y_true)
            ):
                total = len(y_true)

                matching = sum(
                    word == char
                    for word, char in zip(
                        word_predictions,
                        char_predictions,
                    )
                )

                # Compare both models against the same ground truth.
                both_correct = sum(
                    word == truth and char == truth
                    for word, char, truth in zip(
                        word_predictions,
                        char_predictions,
                        y_true,
                    )
                )

                word_only_correct = sum(
                    word == truth and char != truth
                    for word, char, truth in zip(
                        word_predictions,
                        char_predictions,
                        y_true,
                    )
                )

                char_only_correct = sum(
                    word != truth and char == truth
                    for word, char, truth in zip(
                        word_predictions,
                        char_predictions,
                        y_true,
                    )
                )

                both_wrong = sum(
                    word != truth and char != truth
                    for word, char, truth in zip(
                        word_predictions,
                        char_predictions,
                        y_true,
                    )
                )

                result["candidate_prediction_comparison"] = {
                    "total_predictions": total,
                    "matching_predictions": matching,
                    "different_predictions": total - matching,
                    "agreement_rate": (
                        round(matching / total, 4)
                        if total
                        else None
                    ),
                    "both_correct": both_correct,
                    "word_only_correct": word_only_correct,
                    "char_only_correct": char_only_correct,
                    "both_wrong": both_wrong,
                }

                # Additional aggregate diagnostics for spam only.
                if task == "is_spam":
                    result["spam_error_diagnostics"] = (
                        _spam_error_diagnostics(
                            y_true=y_true,
                            rules_predictions=rules_pred,
                            word_predictions=word_predictions,
                            char_predictions=char_predictions,
                        )
                    )

        result["note"] = (
            "These are development cross-validation results, not "
            "frozen-holdout results. Use them for candidate selection; "
            "use the frozen holdout only for final comparison."
        )

        report["tasks"][task] = result

    return report

def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__
    )

    parser.add_argument(
        "--input",
        type=Path,
        default=Path("data/email_labels.csv"),
    )

    parser.add_argument(
        "--split-manifest",
        type=Path,
        default=Path("data/priority_engine_split.json"),
    )

    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "reports/priority_engine_development_cv.json"
        ),
    )

    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--max-folds", type=int, default=5)

    args = parser.parse_args()

    if args.max_folds < 2:
        parser.error("--max-folds must be at least 2")

    if not args.input.is_file():
        parser.error(
            f"Input CSV not found: {args.input}"
        )

    if not args.split_manifest.is_file():
        parser.error(
            f"Frozen split manifest not found: "
            f"{args.split_manifest}"
        )

    try:
        report = evaluate_development(
            args.input,
            args.split_manifest,
            seed=args.seed,
            max_folds=args.max_folds,
        )
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    args.output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    args.output.write_text(
        json.dumps(report, indent=2),
        encoding="utf-8",
    )

    summary = {
        task: {
            "status": item.get("status"),
            "development_rows": item.get("development_rows"),
            "folds": item.get("folds"),
            "rules_macro_f1": item.get(
                "rules", {}
            ).get("macro_f1"),
            "candidate_macro_f1": {
                name: score.get("macro_f1")
                for name, score in item.get(
                    "candidates", {}
                ).items()
            },
            **(
                {
                    "candidate_prediction_comparison": item[
                        "candidate_prediction_comparison"
                    ]
                }
                if "candidate_prediction_comparison" in item
                else {}
            ),
            **(
                {
                    "spam_error_diagnostics": item[
                        "spam_error_diagnostics"
                    ]
                }
                if "spam_error_diagnostics" in item
                else {}
            ),
        }
        for task, item in report["tasks"].items()
    }

    print(
        json.dumps(summary, indent=2)
    )

    print(
        f"Aggregate report written to {args.output}"
    )

    return 0

if __name__ == "__main__":
    raise SystemExit(main())