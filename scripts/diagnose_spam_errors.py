from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from scripts.auto_label_emails import classify_with_rules
from scripts.evaluate_priority_engine import _features, _norm, metrics
from scripts.evaluate_priority_engine_development import (
    _read_manifest,
)
from scripts.priority_engine_splits import row_key


def _tokenize(text: str) -> list[str]:
    """Extract lowercase alphanumeric tokens for aggregate analysis."""
    return re.findall(r"[a-z0-9]+", text.lower())


def _pattern_summary(
    rows: list[dict[str, str]],
    indices: list[int],
    *,
    max_patterns: int = 15,
) -> dict[str, Any]:
    """Summarize frequent tokens without exposing individual messages."""
    token_counts: Counter[str] = Counter()
    document_counts: Counter[str] = Counter()

    for index in indices:
        tokens = set(_tokenize(_features(rows[index])))

        token_counts.update(tokens)
        document_counts.update(tokens)

    # Exclude very short tokens and generic words that offer little insight.
    ignored = {
        "the", "and", "for", "you", "your", "are", "with",
        "this", "that", "from", "have", "has", "was", "were",
        "will", "can", "not", "but", "our", "out", "all",
        "com", "www", "http", "https",
    }

    ranked = [
        {
            "token": token,
            "occurrences": count,
            "emails_containing_token": document_counts[token],
        }
        for token, count in token_counts.most_common()
        if len(token) >= 3 and token not in ignored
    ]

    return {
        "emails_analyzed": len(indices),
        "common_tokens": ranked[:max_patterns],
    }

def _safe_rules_prediction(row: dict[str, str]) -> str:
    """Generate the rules-based spam prediction."""
    output = classify_with_rules(row)
    return _norm(output.get("is_spam"), "is_spam")

def diagnose(
    input_path: Path,
    manifest_path: Path,
    *,
    seed: int = 42,
    max_folds: int = 5,
    max_patterns: int = 15,
) -> dict[str, Any]:
    """Analyze spam errors using development-only out-of-fold predictions."""
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.linear_model import LogisticRegression
        from sklearn.model_selection import StratifiedKFold, cross_val_predict
        from sklearn.pipeline import Pipeline
    except ImportError as exc:
        raise ValueError(
            "Install ML dependencies with: pip install -r requirements-ml.txt"
        ) from exc

    if max_folds < 2:
        raise ValueError("max_folds must be at least 2.")

    if max_patterns < 1:
        raise ValueError("max_patterns must be at least 1.")

    with input_path.open(
        "r",
        newline="",
        encoding="utf-8-sig",
    ) as handle:
        source_rows = list(csv.DictReader(handle))

    frozen = _read_manifest(manifest_path)

    if "is_spam" not in frozen:
        raise ValueError(
            "The frozen manifest has no is_spam task. "
            "The manifest will not be created or modified."
        )

    keys = [row_key(row) for row in source_rows]

    if len(keys) != len(set(keys)):
        raise ValueError(
            "Source CSV contains duplicate row keys."
        )

    # Exclude the frozen holdout before any model fitting or analysis.
    rows: list[dict[str, str]] = []

    for source in source_rows:
        truth = _norm(source.get("true_is_spam"), "is_spam")

        if truth not in {"yes", "no"}:
            continue

        if row_key(source) in frozen["is_spam"]:
            continue

        rows.append(source)

    distribution = Counter(
        _norm(row.get("true_is_spam"), "is_spam")
        for row in rows
    )

    if distribution["yes"] < 2 or distribution["no"] < 2:
        raise ValueError(
            "Need at least two development examples per spam class."
        )

    folds = min(
        max_folds,
        distribution["yes"],
        distribution["no"],
    )

    y_true = [
        _norm(row.get("true_is_spam"), "is_spam")
        for row in rows
    ]

    X = [
        _features(row)
        for row in rows
    ]

    cv = StratifiedKFold(
        n_splits=folds,
        shuffle=True,
        random_state=seed,
    )

    candidates = {
        "word_tfidf": Pipeline([
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
        ]),
        "char_tfidf": Pipeline([
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
        ]),
    }

    predictions: dict[str, list[str]] = {}

    for name, model in candidates.items():
        predictions[name] = cross_val_predict(
            model,
            X,
            y_true,
            cv=cv,
            method="predict",
        ).tolist()

    rules_predictions = [
        _safe_rules_prediction(row)
        for row in rows
    ]

    word_predictions = predictions["word_tfidf"]
    char_predictions = predictions["char_tfidf"]

    shared_error_indices = [
        index
        for index, (truth, word, char) in enumerate(
            zip(y_true, word_predictions, char_predictions)
        )
        if word != truth and char != truth
    ]

    shared_spam_missed = [
        index
        for index in shared_error_indices
        if y_true[index] == "yes"
    ]

    shared_legitimate_flagged = [
        index
        for index in shared_error_indices
        if y_true[index] == "no"
    ]

    rules_rescue = [
        index
        for index in shared_error_indices
        if rules_predictions[index] == y_true[index]
    ]

    rules_also_wrong = [
        index
        for index in shared_error_indices
        if rules_predictions[index] != y_true[index]
    ]

    # Patterns are calculated only for groups with at least two examples.
    def summarize_group(indices: list[int]) -> dict[str, Any]:
        if len(indices) < 2:
            return {
                "emails_analyzed": len(indices),
                "common_tokens": [],
                "note": (
                    "At least two examples are needed to produce "
                    "a useful aggregate pattern summary."
                ),
            }

        return _pattern_summary(
            rows,
            indices,
            max_patterns=max_patterns,
        )

    report = {
        "evaluation_method": (
            "Stratified out-of-fold predictions on development rows only"
        ),
        "holdout_policy": (
            "Frozen spam test IDs are excluded before fitting. "
            "The manifest is read-only."
        ),
        "privacy_note": (
            "No raw email content, sender addresses, or message IDs "
            "are emitted. Token summaries are aggregate development "
            "statistics and may still reveal sensitive themes."
        ),
        "development_rows": len(rows),
        "frozen_holdout_rows_excluded": (
            sum(
                1
                for row in source_rows
                if row_key(row) in frozen["is_spam"]
            )
        ),
        "class_distribution": dict(distribution),
        "folds": folds,
        "metrics": {
            "rules": metrics(
                y_true,
                rules_predictions,
                ("yes", "no"),
            ),
            "word_tfidf": metrics(
                y_true,
                word_predictions,
                ("yes", "no"),
            ),
            "char_tfidf": metrics(
                y_true,
                char_predictions,
                ("yes", "no"),
            ),
        },
        "shared_error_counts": {
            "total": len(shared_error_indices),
            "actual_spam_missed_by_both": len(shared_spam_missed),
            "legitimate_emails_flagged_by_both": len(
                shared_legitimate_flagged
            ),
            "rules_correct_on_shared_errors": len(rules_rescue),
            "rules_also_wrong_on_shared_errors": len(rules_also_wrong),
        },
        "shared_error_pattern_analysis": {
            "spam_missed_by_both": summarize_group(
                shared_spam_missed
            ),
            "legitimate_emails_flagged_by_both": summarize_group(
                shared_legitimate_flagged
            ),
            "errors_where_rules_are_correct": summarize_group(
                rules_rescue
            ),
            "errors_where_all_three_are_wrong": summarize_group(
                rules_also_wrong
            ),
        },
        "note": (
            "Token frequency indicates association, not causation. "
            "Small groups may produce unstable patterns. "
            "Do not tune against the frozen holdout."
        ),
    }

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
        default=Path("reports/spam_error_diagnostics.json"),
    )

    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--max-folds", type=int, default=5)
    parser.add_argument("--max-patterns", type=int, default=15)

    args = parser.parse_args()

    if not args.input.is_file():
        parser.error(
            f"Input CSV not found: {args.input}"
        )

    if not args.split_manifest.is_file():
        parser.error(
            f"Frozen split manifest not found: {args.split_manifest}"
        )

    try:
        report = diagnose(
            args.input,
            args.split_manifest,
            seed=args.seed,
            max_folds=args.max_folds,
            max_patterns=args.max_patterns,
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

    print(
        json.dumps(
            {
                "development_rows": report["development_rows"],
                "frozen_holdout_rows_excluded": (
                    report["frozen_holdout_rows_excluded"]
                ),
                "shared_error_counts": report["shared_error_counts"],
                "output_file": str(args.output),
            },
            indent=2,
        )
    )

    print(
        "Detailed aggregate diagnostics written to "
        f"{args.output}"
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())