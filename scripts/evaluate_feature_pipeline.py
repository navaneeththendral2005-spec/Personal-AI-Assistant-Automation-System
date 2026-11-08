"""Compare FRIDAY's text-only baseline with the text-plus-metadata pipeline.

Run from the project root (needs scikit-learn: ``pip install -r requirements-ml.txt``):

    python -m scripts.evaluate_feature_pipeline

Variants compared for every task
--------------------------------
text_only_legacy                    the existing TF-IDF + logistic-regression baseline
text_only_normalized                same model, normalised text (isolates normalisation)
text_plus_metadata                  TF-IDF + structured features (headline result;
                                    Gmail labels are NOT used, so it stays independent)
text_plus_metadata_gmail_assisted   ablation only: adds Google's own classifier labels

Evaluation protocol
-------------------
* Verified labels only (``true_*``). Predictions are never ground truth.
* The frozen test IDs are read from the existing private manifest **read-only**:
  this script never creates, edits, or repairs it. If the manifest (or a task entry)
  is missing, only development cross-validation is reported.
* Models are trained on development rows only; ``train_model`` aborts if any training
  row is a frozen test row. Truth, adjudication, and prediction columns cannot reach
  the feature code (see ``backend.services.email_features``).
* Stratified cross-validation on development rows, then ONE fit on all development
  rows scored once on the frozen test rows, with a paired bootstrap against baseline.
* Optional ``--tune-priority`` runs nested development-only CV over word/character
  TF-IDF, Logistic Regression C, and class-weight settings. Pair it with
  ``--development-only`` to keep the frozen test completely unevaluated during tuning.
* The report contains aggregate numbers only: no IDs, senders, subjects, or bodies.
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from backend.services.email_features import (
    ALLOWED_INPUT_COLUMNS,
    FEATURE_NAMES,
    find_forbidden_columns,
    context_for_rows,
    parse_timestamp,
    schema_fingerprint,
)
from backend.services.priority_model import DEFAULT_METADATA_WEIGHT, train_model
from scripts.evaluate_priority_engine import TASKS, _norm, _truth_key, metrics
from scripts.priority_engine_splits import MANIFEST_VERSION, row_key

PROJECT_ROOT = Path.cwd()
DEFAULT_INPUT = PROJECT_ROOT / "data" / "email_labels_evaluation.csv"
DEFAULT_MANIFEST = PROJECT_ROOT / "data" / "priority_engine_split.json"
DEFAULT_JSON = PROJECT_ROOT / "reports" / "feature_pipeline_evaluation.json"
DEFAULT_MD = PROJECT_ROOT / "reports" / "feature_pipeline_evaluation.md"

BASELINE = "text_only_legacy"
EXPERIMENTS: tuple[tuple[str, str, str], ...] = (
    # (name, pipeline variant, Gmail label mode)
    ("text_only_legacy", "text_only_legacy", "independent"),
    ("text_only_normalized", "text_only_normalized", "independent"),
    ("text_word_char", "text_word_char", "independent"),
    ("text_plus_metadata", "text_plus_metadata", "independent"),
    ("text_plus_metadata_gmail_assisted", "text_plus_metadata", "gmail_assisted"),
)
SMALL_TEST_SET = 30
# A metadata variant is only recommended for a task when it beats the baseline's
# development cross-validation macro-F1 by at least this margin. The frozen test set
# is never used to choose a variant.
RECOMMENDATION_MARGIN = 0.03


# Pre-declared search space. Hyperparameters are selected inside each outer
# development fold, so the outer predictions remain an honest estimate.
PRIORITY_CANDIDATES: tuple[dict[str, Any], ...] = (
    {"variant": "text_only_legacy", "c": 1.0, "class_weight": "balanced"},
    {"variant": "text_only_legacy", "c": 0.1, "class_weight": "balanced"},
    {"variant": "text_only_legacy", "c": 10.0, "class_weight": "balanced"},
    {"variant": "text_word_char", "c": 0.1, "class_weight": "balanced"},
    {"variant": "text_word_char", "c": 1.0, "class_weight": "balanced"},
    {"variant": "text_word_char", "c": 10.0, "class_weight": "balanced"},
    {"variant": "text_word_char", "c": 0.1, "class_weight": None},
    {"variant": "text_word_char", "c": 1.0, "class_weight": None},
    {"variant": "text_word_char", "c": 10.0, "class_weight": None},
)
MIN_CLASS_SUPPORT_FOR_THRESHOLD_TUNING = 10


class FeatureEvaluationError(ValueError):
    """A safe-to-display input or manifest problem."""


# --------------------------------------------------------------------------- #
# Input handling
# --------------------------------------------------------------------------- #

def read_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    if not path.is_file():
        raise FeatureEvaluationError(f"Dataset not found: {path}")
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            headers = list(reader.fieldnames or [])
            rows = [{k: (v or "") for k, v in row.items() if k is not None} for row in reader]
    except (OSError, csv.Error) as exc:
        raise FeatureEvaluationError(f"Could not read dataset ({type(exc).__name__}).") from exc
    if not headers:
        raise FeatureEvaluationError("Dataset has no header row.")
    return headers, rows


def read_frozen_test_ids(manifest_path: Path, task: str) -> set[str] | None:
    """Read frozen test IDs without ever creating or modifying the manifest.

    Returns ``None`` when the manifest or the task entry does not exist.
    """
    if not manifest_path.is_file():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FeatureEvaluationError(
            f"Split manifest is unreadable ({type(exc).__name__}). Back it up and repair "
            "it manually; this script never regenerates the frozen test set."
        ) from exc
    if (
        not isinstance(manifest, dict)
        or manifest.get("version") != MANIFEST_VERSION
        or not isinstance(manifest.get("tasks"), dict)
    ):
        raise FeatureEvaluationError("Unsupported or malformed split manifest.")
    entry = manifest["tasks"].get(task)
    if entry is None:
        return None
    ids = entry.get("test_ids") if isinstance(entry, dict) else None
    if not isinstance(ids, list) or not all(isinstance(i, str) and i.strip() for i in ids):
        raise FeatureEvaluationError(f"Malformed test_ids for task {task!r} in the manifest.")
    return set(ids)


def verified_rows(rows: list[dict[str, str]], task: str) -> list[dict[str, Any]]:
    """Rows with a valid verified label for ``task`` (legacy SPAM excluded from priority)."""
    config = TASKS[task]
    valid = {_truth_key(label, task) for label in config["labels"]}
    selected: list[dict[str, Any]] = []
    for row in rows:
        truth = _norm(row.get(config["truth"]), task)
        if not truth or truth not in valid:
            continue
        if task == "priority" and truth == "SPAM":
            continue
        selected.append({"row": row, "truth": truth, "key": row_key(row)})
    return selected


def _distribution(items: list[dict[str, Any]]) -> dict[str, int]:
    return dict(sorted(Counter(item["truth"] for item in items).items()))


def column_coverage(headers: list[str], rows: list[dict[str, str]]) -> dict[str, Any]:
    """Which optional metadata columns exist and how often they hold a value."""
    optional = [c for c in ALLOWED_INPUT_COLUMNS if c not in {"sender", "subject", "snippet", "body_text"}]
    coverage: dict[str, Any] = {}
    for column in optional:
        if column not in headers:
            coverage[column] = {"column_present": False, "non_empty_fraction": 0.0}
            continue
        filled = sum(1 for row in rows if (row.get(column) or "").strip())
        coverage[column] = {
            "column_present": True,
            "non_empty_fraction": round(filled / len(rows), 4) if rows else 0.0,
        }
    stamps = [
        1 for row in rows
        if any(parse_timestamp(row.get(k)) for k in ("message_date", "date", "received_at"))
    ]
    coverage["parseable_timestamp_fraction"] = round(len(stamps) / len(rows), 4) if rows else 0.0
    return coverage


# --------------------------------------------------------------------------- #
# Evaluation
# --------------------------------------------------------------------------- #

def _fit(train_items, task, variant, mode, seed, frozen_ids, reference_time, weight):
    return train_model(
        [item["row"] for item in train_items],
        [item["truth"] for item in train_items],
        keys=[item["key"] for item in train_items],
        frozen_test_ids=frozen_ids,
        variant=variant,
        seed=seed,
        task=task,
        gmail_label_mode=mode,
        reference_time=reference_time,
        metadata_weight=weight,
    )


def _cross_validate(dev, task, labels, *, folds, seed, frozen_ids, reference_time, weight):
    from sklearn.model_selection import StratifiedKFold

    counts = Counter(item["truth"] for item in dev)
    n_splits = min(folds, min(counts.values())) if len(counts) >= 2 else 0
    if n_splits < 2:
        return {
            "status": "insufficient_data_for_cross_validation",
            "message": "Need at least two classes with two or more development examples each.",
        }
    splitter = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=seed)
    y = [item["truth"] for item in dev]
    results: dict[str, Any] = {}
    for name, variant, mode in EXPERIMENTS:
        predictions = [""] * len(dev)
        try:
            for train_idx, test_idx in splitter.split(dev, y):
                model = _fit(
                    [dev[i] for i in train_idx], task, variant, mode, seed,
                    frozen_ids, reference_time, weight,
                )
                predicted = model.predict([dev[i]["row"] for i in test_idx])
                for index, label in zip(test_idx, predicted):
                    predictions[index] = label
        except Exception as exc:  # report, never crash the whole comparison
            results[name] = {"status": "failed", "error_type": type(exc).__name__}
            continue
        results[name] = metrics(y, predictions, labels)
    return {"status": "ok", "folds": n_splits, "variants": results}


def _priority_nested_cv(dev, labels, *, folds, seed, frozen_ids):
    """Tune priority text/model settings inside development-only nested CV."""
    from sklearn.model_selection import StratifiedKFold

    y = [item["truth"] for item in dev]
    counts = Counter(y)
    outer_folds = min(folds, min(counts.values())) if len(counts) >= 2 else 0
    if outer_folds < 2:
        return {"status": "insufficient_data_for_nested_cv"}

    baseline_predictions = [""] * len(dev)
    tuned_predictions = [""] * len(dev)
    chosen: list[dict[str, Any]] = []
    observed_inner_folds: list[int] = []
    outer = StratifiedKFold(n_splits=outer_folds, shuffle=True, random_state=seed)

    for outer_train_idx, outer_valid_idx in outer.split(dev, y):
        outer_train = [dev[i] for i in outer_train_idx]
        outer_valid = [dev[i] for i in outer_valid_idx]
        inner_y = [item["truth"] for item in outer_train]
        inner_counts = Counter(inner_y)
        inner_folds = min(3, min(inner_counts.values())) if len(inner_counts) >= 2 else 0
        if inner_folds < 2:
            return {"status": "insufficient_data_for_inner_cv"}
        observed_inner_folds.append(inner_folds)
        inner = StratifiedKFold(n_splits=inner_folds, shuffle=True, random_state=seed)

        candidate_scores: list[tuple[float, dict[str, Any]]] = []
        for config in PRIORITY_CANDIDATES:
            scores: list[float] = []
            for train_idx, valid_idx in inner.split(outer_train, inner_y):
                train_items = [outer_train[i] for i in train_idx]
                valid_items = [outer_train[i] for i in valid_idx]
                model = train_model(
                    [item["row"] for item in train_items],
                    [item["truth"] for item in train_items],
                    keys=[item["key"] for item in train_items],
                    frozen_test_ids=frozen_ids, variant=config["variant"], seed=seed,
                    task="priority", gmail_label_mode="independent", c=config["c"],
                    class_weight=config["class_weight"],
                )
                predicted = model.predict([item["row"] for item in valid_items])
                scores.append(metrics(
                    [item["truth"] for item in valid_items], predicted, labels
                )["macro_f1"])
            candidate_scores.append((sum(scores) / len(scores), config))

        # Stable ordering means ties favour the simpler legacy baseline.
        best_score, best_config = max(candidate_scores, key=lambda pair: pair[0])
        chosen.append({**best_config, "inner_macro_f1": round(best_score, 4)})

        baseline = train_model(
            [item["row"] for item in outer_train],
            [item["truth"] for item in outer_train],
            keys=[item["key"] for item in outer_train], frozen_test_ids=frozen_ids,
            variant="text_only_legacy", seed=seed, task="priority", c=1.0,
            class_weight="balanced",
        )
        tuned = train_model(
            [item["row"] for item in outer_train],
            [item["truth"] for item in outer_train],
            keys=[item["key"] for item in outer_train], frozen_test_ids=frozen_ids,
            variant=best_config["variant"], seed=seed, task="priority",
            c=best_config["c"], class_weight=best_config["class_weight"],
        )
        baseline_fold = baseline.predict([item["row"] for item in outer_valid])
        tuned_fold = tuned.predict([item["row"] for item in outer_valid])
        for index, prediction in zip(outer_valid_idx, baseline_fold):
            baseline_predictions[index] = prediction
        for index, prediction in zip(outer_valid_idx, tuned_fold):
            tuned_predictions[index] = prediction

    baseline_metrics = metrics(y, baseline_predictions, labels)
    tuned_metrics = metrics(y, tuned_predictions, labels)
    # Select the final fit configuration by outer-fold selection frequency; break
    # ties by mean inner-CV score. This choice uses development data only.
    config_groups: dict[tuple[str, float, str | None], list[float]] = {}
    for item in chosen:
        key = (item["variant"], float(item["c"]), item["class_weight"])
        config_groups.setdefault(key, []).append(float(item["inner_macro_f1"]))
    final_key = max(
        config_groups,
        key=lambda key: (len(config_groups[key]), sum(config_groups[key]) / len(config_groups[key])),
    )
    final_config = {"variant": final_key[0], "c": final_key[1], "class_weight": final_key[2]}
    support = {label: counts.get(label, 0) for label in labels}
    threshold_ready = all(value >= MIN_CLASS_SUPPORT_FOR_THRESHOLD_TUNING for value in support.values())
    return {
        "status": "ok",
        "protocol": "nested stratified CV; hyperparameters selected only inside each outer training fold",
        "outer_folds": outer_folds,
        "inner_folds": min(observed_inner_folds),
        "baseline": baseline_metrics,
        "tuned": tuned_metrics,
        "macro_f1_delta": round(tuned_metrics["macro_f1"] - baseline_metrics["macro_f1"], 4),
        "selected_config_by_outer_fold": chosen,
        "final_config_selected_from_development": final_config,
        "threshold_tuning": {
            "status": "not_run" if threshold_ready else "deferred_insufficient_class_support",
            "minimum_examples_per_class": MIN_CLASS_SUPPORT_FOR_THRESHOLD_TUNING,
            "development_class_support": support,
            "reason": (
                "Threshold calibration is not applied in this pass. It remains deferred until every class "
                "has sufficient development support; with rare classes, tuning thresholds would overfit "
                "a handful of examples."
            ),
        },
    }


def recommend_variant(cv: dict[str, Any]) -> dict[str, Any]:
    """Pick a variant from development cross-validation only (never the test set)."""
    basis = (
        f"development cross-validation macro-F1; a candidate must beat the baseline by "
        f">= {RECOMMENDATION_MARGIN}"
    )
    if cv.get("status") != "ok":
        return {"variant": BASELINE, "basis": basis, "reason": "cross-validation unavailable"}
    scores = {
        name: result.get("macro_f1")
        for name, result in cv["variants"].items()
        if isinstance(result, dict) and result.get("macro_f1") is not None
        and name != "text_plus_metadata_gmail_assisted"  # ablation, never recommended
    }
    base = scores.get(BASELINE)
    if base is None:
        return {"variant": BASELINE, "basis": basis, "reason": "baseline score unavailable"}
    best_name, best_score = max(scores.items(), key=lambda item: item[1])
    if best_name != BASELINE and best_score - base >= RECOMMENDATION_MARGIN:
        return {"variant": best_name, "basis": basis, "macro_f1_gain": round(best_score - base, 4)}
    return {"variant": BASELINE, "basis": basis, "reason": "no candidate beat the baseline by the margin"}


def _paired_bootstrap(y_true, baseline, candidate, labels, *, resamples, seed):
    n = len(y_true)
    if n < 5 or resamples < 1:
        return {"status": "skipped_too_few_samples"}
    rng = random.Random(seed)
    deltas = []
    for _ in range(resamples):
        idx = [rng.randrange(n) for _ in range(n)]
        sample_true = [y_true[i] for i in idx]
        new = metrics(sample_true, [candidate[i] for i in idx], labels)["macro_f1"]
        old = metrics(sample_true, [baseline[i] for i in idx], labels)["macro_f1"]
        deltas.append(new - old)
    deltas.sort()
    return {
        "status": "ok",
        "resamples": resamples,
        "mean_macro_f1_delta": round(sum(deltas) / len(deltas), 4),
        "ci95": [
            round(deltas[int(0.025 * len(deltas))], 4),
            round(deltas[min(int(0.975 * len(deltas)), len(deltas) - 1)], 4),
        ],
        "share_of_resamples_improved": round(sum(d > 0 for d in deltas) / len(deltas), 4),
    }


def _frozen_test(dev, test, task, labels, *, seed, frozen_ids, reference_time, weight, bootstrap, priority_config=None):
    y_true = [item["truth"] for item in test]
    if len({item["truth"] for item in dev}) < 2:
        return {"status": "insufficient_training_classes"}
    predictions: dict[str, list[str]] = {}
    results: dict[str, Any] = {}
    for name, variant, mode in EXPERIMENTS:
        try:
            model = _fit(dev, task, variant, mode, seed, frozen_ids, reference_time, weight)
            predictions[name] = model.predict([item["row"] for item in test])
        except Exception as exc:
            results[name] = {"status": "failed", "error_type": type(exc).__name__}
            continue
        results[name] = metrics(y_true, predictions[name], labels)

    if task == "priority" and priority_config:
        try:
            tuned_model = train_model(
                [item["row"] for item in dev],
                [item["truth"] for item in dev],
                keys=[item["key"] for item in dev], frozen_test_ids=frozen_ids,
                variant=priority_config["variant"], seed=seed, task="priority",
                c=priority_config["c"], class_weight=priority_config["class_weight"],
            )
            predictions["nested_tuned"] = tuned_model.predict([item["row"] for item in test])
            results["nested_tuned"] = metrics(y_true, predictions["nested_tuned"], labels)
        except Exception as exc:
            results["nested_tuned"] = {"status": "failed", "error_type": type(exc).__name__}

    paired: dict[str, Any] = {}
    if BASELINE in predictions:
        for name, predicted in predictions.items():
            if name != BASELINE:
                paired[name] = _paired_bootstrap(
                    y_true, predictions[BASELINE], predicted, labels,
                    resamples=bootstrap, seed=seed,
                )
    return {
        "status": "ok",
        "test_samples": len(test),
        "variants": results,
        "paired_bootstrap_vs_baseline": paired,
    }


def _interpretation(task: str, test_n: int, frozen: dict[str, Any]) -> list[str]:
    notes: list[str] = []
    if test_n == 0:
        notes.append("No frozen test rows for this task: only development cross-validation is meaningful.")
        return notes
    step = round(100.0 / test_n, 1)
    if test_n < SMALL_TEST_SET:
        notes.append(
            f"The frozen test set has only {test_n} rows, so one email moves accuracy by "
            f"{step} percentage points. Treat test differences as exploratory, not proof."
        )
    paired = frozen.get("paired_bootstrap_vs_baseline", {}) if isinstance(frozen, dict) else {}
    candidate = paired.get("text_plus_metadata", {})
    if candidate.get("status") == "ok":
        low, high = candidate["ci95"]
        if low <= 0.0 <= high:
            notes.append(
                "The 95% bootstrap interval for the macro-F1 change of text_plus_metadata "
                "includes zero: no reliable improvement over the baseline is established."
            )
        elif low > 0:
            notes.append("text_plus_metadata improved macro-F1 over the baseline beyond bootstrap noise.")
        else:
            notes.append("text_plus_metadata was reliably worse than the baseline on this test set.")
    return notes


def evaluate_feature_pipeline(
    input_path: Path,
    manifest_path: Path,
    *,
    seed: int = 42,
    folds: int = 5,
    metadata_weight: float = DEFAULT_METADATA_WEIGHT,
    bootstrap: int = 500,
    tasks: tuple[str, ...] | None = None,
    tune_priority: bool = False,
    development_only: bool = False,
) -> dict[str, Any]:
    headers, rows = read_rows(input_path)
    selected_tasks = tasks or tuple(TASKS)
    unknown = [t for t in selected_tasks if t not in TASKS]
    if unknown:
        raise FeatureEvaluationError(f"Unknown task(s): {unknown}")

    context = context_for_rows(rows)
    reference_time = context.reference_time
    forbidden = find_forbidden_columns(headers)

    report: dict[str, Any] = {
        "input_file_name": input_path.name,
        "split_manifest_file_name": manifest_path.name,
        "seed": seed,
        "metadata_weight": metadata_weight,
        "bootstrap_resamples": bootstrap,
        "feature_schema": {
            "fingerprint": schema_fingerprint(),
            "feature_count": len(FEATURE_NAMES),
        },
        "reference_time_utc": reference_time.isoformat() if reference_time else None,
        "optional_metadata_coverage": column_coverage(headers, rows),
        "leakage_controls": {
            "forbidden_columns_present_in_csv": len(forbidden),
            "forbidden_columns_reach_features": False,
            "frozen_manifest_is_read_only": True,
            "gmail_labels_in_headline_variant": False,
            "note": (
                "text_plus_metadata ignores Gmail labels. The gmail_assisted variant is an "
                "ablation that uses Google's own classifier labels and must not be reported "
                "as FRIDAY's independent accuracy."
            ),
        },
        "protocol": (
            "Stratified cross-validation on development rows; one fit on all development "
            "rows scored once on the frozen test rows; paired bootstrap vs the legacy baseline."
        ),
        "privacy_note": "Aggregate numbers only: no IDs, senders, subjects, or bodies.",
        "tasks": {},
    }

    for task in selected_tasks:
        labels = TASKS[task]["labels"]
        items = verified_rows(rows, task)
        entry: dict[str, Any] = {"verified_labels": len(items)}
        if len(items) < 4 or len({i["truth"] for i in items}) < 2:
            entry["status"] = "insufficient_verified_labels"
            report["tasks"][task] = entry
            continue

        frozen_ids = read_frozen_test_ids(manifest_path, task)
        if frozen_ids is None:
            test: list[dict[str, Any]] = []
            dev = items
            entry["frozen_split"] = "not_found"
        else:
            test = [i for i in items if i["key"] in frozen_ids]
            dev = [i for i in items if i["key"] not in frozen_ids]
            entry["frozen_split"] = "read_only"
        entry["dataset"] = {
            "verified": len(items),
            "development": len(dev),
            "frozen_test": len(test),
            "class_distribution": _distribution(items),
            "development_distribution": _distribution(dev),
            "frozen_test_distribution": _distribution(test),
        }

        entry["development_cross_validation"] = _cross_validate(
            dev, task, labels, folds=folds, seed=seed, frozen_ids=frozen_ids,
            reference_time=reference_time, weight=metadata_weight,
        )
        if task == "priority" and tune_priority:
            entry["priority_model_tuning"] = _priority_nested_cv(
                dev, labels, folds=min(folds, 4), seed=seed, frozen_ids=frozen_ids,
            )
        if development_only:
            entry["frozen_test"] = {"status": "skipped_by_request_development_only"}
        elif test:
            entry["frozen_test"] = _frozen_test(
                dev, test, task, labels, seed=seed, frozen_ids=frozen_ids,
                reference_time=reference_time, weight=metadata_weight, bootstrap=bootstrap,
                priority_config=(
                    entry.get("priority_model_tuning", {}).get("final_config_selected_from_development")
                    if task == "priority" and tune_priority else None
                ),
            )
        else:
            entry["frozen_test"] = {"status": "no_frozen_test_rows"}
        entry["recommendation"] = recommend_variant(entry["development_cross_validation"])
        entry["status"] = "ok"
        entry["interpretation"] = _interpretation(task, len(test), entry["frozen_test"])
        report["tasks"][task] = entry
    return report


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #

def _fmt(value: Any) -> str:
    return "n/a" if value is None else (f"{value:.3f}" if isinstance(value, float) else str(value))


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# FRIDAY feature-pipeline evaluation",
        "",
        "Text-only baseline vs text + structured metadata. Aggregate numbers only.",
        "",
        f"- Feature schema: `{report['feature_schema']['fingerprint']}` "
        f"({report['feature_schema']['feature_count']} features)",
        f"- Metadata weight: {report['metadata_weight']}  |  Seed: {report['seed']}",
        f"- {report['leakage_controls']['note']}",
        "",
        "## Optional metadata coverage in the dataset",
        "",
        "| Column | Present | Non-empty |",
        "|---|---|---|",
    ]
    for column, info in report["optional_metadata_coverage"].items():
        if isinstance(info, dict):
            lines.append(f"| {column} | {info['column_present']} | {info['non_empty_fraction']:.0%} |")
    lines.append(f"\nParseable timestamps: {report['optional_metadata_coverage']['parseable_timestamp_fraction']:.0%}. "
                 "Timestamp, thread, recipient and attachment features are inactive until these columns are filled "
                 "(see scripts/enrich_dataset_metadata.py).\n")

    for task, entry in report["tasks"].items():
        lines += [f"## {task}", ""]
        if entry.get("status") != "ok":
            lines += [f"Status: {entry.get('status')} (verified labels: {entry.get('verified_labels')})", ""]
            continue
        data = entry["dataset"]
        lines += [
            f"Verified {data['verified']} | development {data['development']} | frozen test {data['frozen_test']}  ",
            f"Class distribution: {data['class_distribution']}  ",
            f"Frozen test distribution: {data['frozen_test_distribution']}",
            "",
        ]
        cv = entry["development_cross_validation"]
        if cv.get("status") == "ok":
            lines += [f"### Development cross-validation ({cv['folds']}-fold, out-of-fold)", "",
                      "| Variant | Accuracy | Macro-F1 |", "|---|---|---|"]
            for name, result in cv["variants"].items():
                lines.append(f"| {name} | {_fmt(result.get('accuracy'))} | {_fmt(result.get('macro_f1'))} |")
            lines.append("")
        else:
            lines += [f"Cross-validation: {cv.get('status')}", ""]
        tuning = entry.get("priority_model_tuning")
        if isinstance(tuning, dict) and tuning.get("status") == "ok":
            lines += ["### Nested development-only model tuning", "",
                      "| Model | Accuracy | Macro-F1 |", "|---|---:|---:|"]
            for name in ("baseline", "tuned"):
                score = tuning[name]
                lines.append(f"| {name} | {_fmt(score.get('accuracy'))} | {_fmt(score.get('macro_f1'))} |")
            lines.append(
                f"\nMacro-F1 change: **{tuning['macro_f1_delta']:+.3f}**. "
                f"Development-selected final config: `{tuning['final_config_selected_from_development']}`. "
                f"Threshold tuning: `{tuning['threshold_tuning']['status']}`.\n"
            )
        frozen = entry["frozen_test"]
        if frozen.get("status") == "ok":
            lines += [f"### Frozen test ({frozen['test_samples']} rows)", "",
                      "| Variant | Accuracy | Macro-F1 | Macro-F1 change vs baseline (95% CI) |", "|---|---|---|---|"]
            for name, result in frozen["variants"].items():
                paired = frozen["paired_bootstrap_vs_baseline"].get(name, {})
                change = (
                    f"{paired['mean_macro_f1_delta']:+.3f} ({paired['ci95'][0]:+.3f} to {paired['ci95'][1]:+.3f})"
                    if paired.get("status") == "ok" else "baseline" if name == BASELINE else "n/a"
                )
                lines.append(f"| {name} | {_fmt(result.get('accuracy'))} | {_fmt(result.get('macro_f1'))} | {change} |")
            lines += ["", "Per-class F1 / recall (support) on the frozen test set:", "",
                      "| Class | Support | Baseline F1 | Baseline recall | Metadata F1 | Metadata recall |", "|---|---|---|---|---|---|"]
            base = frozen["variants"].get(BASELINE, {}).get("per_class", {})
            meta = frozen["variants"].get("text_plus_metadata", {}).get("per_class", {})
            for label in base:
                lines.append(
                    f"| {label} | {base[label]['support']} | {_fmt(base[label]['f1'])} | {_fmt(base[label]['recall'])} | "
                    f"{_fmt(meta.get(label, {}).get('f1'))} | {_fmt(meta.get(label, {}).get('recall'))} |"
                )
            lines.append("")
        else:
            lines += [f"Frozen test: {frozen.get('status')}", ""]
        rec = entry.get("recommendation", {})
        if rec:
            gain = f" (+{rec['macro_f1_gain']:.3f} macro-F1 in development CV)" if "macro_f1_gain" in rec else ""
            lines.append(f"**Recommended variant (development CV only): `{rec['variant']}`{gain}.**\n")
        for note in entry.get("interpretation", []):
            lines.append(f"> {note}")
        lines.append("")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output-json", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--output-md", type=Path, default=DEFAULT_MD)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--metadata-weight", type=float, default=DEFAULT_METADATA_WEIGHT)
    parser.add_argument("--bootstrap", type=int, default=500)
    parser.add_argument("--task", action="append", choices=sorted(TASKS), help="Limit to one or more tasks.")
    parser.add_argument("--tune-priority", action="store_true", help="Run nested development-only priority hyperparameter tuning.")
    parser.add_argument("--development-only", action="store_true", help="Do not evaluate the frozen test set; use while tuning.")
    args = parser.parse_args(argv)

    try:
        report = evaluate_feature_pipeline(
            args.input, args.manifest, seed=args.seed, folds=args.folds,
            metadata_weight=args.metadata_weight, bootstrap=args.bootstrap,
            tasks=tuple(args.task) if args.task else None,
            tune_priority=args.tune_priority,
            development_only=args.development_only,
        )
    except FeatureEvaluationError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    args.output_md.write_text(render_markdown(report), encoding="utf-8")
    print(f"Wrote {args.output_json} and {args.output_md} (aggregate numbers only).")
    for task, entry in report["tasks"].items():
        print(f"- {task}: {entry.get('status')} (verified labels: {entry.get('verified_labels')})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
