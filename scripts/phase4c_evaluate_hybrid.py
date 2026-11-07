"""Controlled rules-only, Ollama-only, and hybrid evaluation on frozen human-label holdouts.

Reads private email rows locally; writes aggregate-only reports. No predictions or
identifiers are written back to the source data. Each unique email is inferred at
most once per strategy, even when it has multiple evaluated task labels.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Mapping

from backend.services.email_classifier import classify_email
from backend.services.ollama_classifier import (
    Classification, OllamaConfig, OllamaError, classify_with_ollama,
)
from scripts.priority_engine_splits import row_key

TASKS = {
    "category": ("true_category", ("JOB", "FINANCE", "PERSONAL", "PROMOTIONS", "UPDATES", "OTHER")),
    "needs_response": ("true_needs_response", ("yes", "no")),
}


def norm(value: Any, task: str) -> str:
    value = str(value or "").strip()
    return value.lower() if task == "needs_response" else value.upper()


def score(truth, predictions, labels):
    """Metrics with invalid/missing outputs counted as errors, not dropped."""
    n = len(truth)
    per_class = {}
    for label in labels:
        tp = sum(t == label and p == label for t, p in zip(truth, predictions))
        fp = sum(t != label and p == label for t, p in zip(truth, predictions))
        fn = sum(t == label and p != label for t, p in zip(truth, predictions))
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        per_class[label] = {
            "precision": round(precision, 4), "recall": round(recall, 4),
            "f1": round(f1, 4), "support": sum(t == label for t in truth),
        }
    valid = sum(p in labels for p in predictions)
    return {
        "samples": n,
        "accuracy": round(sum(t == p for t, p in zip(truth, predictions)) / n, 4) if n else None,
        "macro_f1": round(statistics.mean(v["f1"] for v in per_class.values()), 4) if per_class else None,
        "weighted_f1": round(sum(v["f1"] * v["support"] for v in per_class.values()) / n, 4) if n else None,
        "prediction_coverage": round(valid / n, 4) if n else None,
        "per_class": per_class,
        "confusion_matrix": {
            actual: {
                predicted: sum(
                    t == actual and (p if p in labels else "INVALID_OR_MISSING") == predicted
                    for t, p in zip(truth, predictions)
                )
                for predicted in (*labels, "INVALID_OR_MISSING")
            }
            for actual in labels
        },
    }


def _email(row):
    return SimpleNamespace(
        sender=row.get("sender", ""), subject=row.get("subject", ""),
        snippet=row.get("snippet", ""), body_text=row.get("body_text", ""),
        labels=row.get("labels", "").split() if isinstance(row.get("labels"), str) else [],
    )


def _rules_prediction(result, task):
    if task == "category":
        return norm(result.get("category"), task)
    value = result.get("requires_response")
    return "yes" if value is True else "no" if value is False else ""


def _model_prediction(result: Mapping[str, Any] | None, task: str):
    return norm(result.get(task), task) if result else ""


def _classification_dict(result):
    return result.as_dict() if isinstance(result, Classification) else dict(result)


def evaluate(
    input_path: Path, manifest_path: Path, output_path: Path, threshold: float,
    ollama_config: OllamaConfig, enabled: bool = True, *,
    rules_classifier: Callable = classify_email,
    ollama_classifier: Callable = classify_with_ollama,
) -> dict:
    if not 0 <= threshold <= 100:
        raise ValueError("threshold must be between 0 and 100")
    with input_path.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError("Input CSV is empty")
    if not manifest_path.exists():
        raise ValueError("Frozen split manifest is missing; evaluator will not create one.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = manifest.get("tasks")
    if not isinstance(entries, dict):
        raise ValueError("Malformed frozen split manifest")

    eligible: dict[str, dict] = {}
    task_rows: dict[str, list[tuple[str, str]]] = {}
    for task, (truth_col, labels) in TASKS.items():
        entry = entries.get(task)
        if not isinstance(entry, dict) or not isinstance(entry.get("test_ids"), list):
            task_rows[task] = []
            continue
        test_ids = set(entry["test_ids"])
        pairs = []
        for row in rows:
            truth = norm(row.get(truth_col), task)
            if truth in labels:
                key = row_key(row)
                if key in test_ids:
                    eligible.setdefault(key, row)
                    pairs.append((key, truth))
        task_rows[task] = pairs

    report = {
        "schema_version": 2,
        "evaluation": "phase4c_rules_vs_ollama_vs_hybrid",
        "threshold": threshold,
        "ollama_model": ollama_config.model,
        "ground_truth": "Human true_* columns only; existing frozen task manifests only",
        "privacy": "Aggregate-only. No email identifiers, sender, subject, body, or row-level predictions are written.",
        "tasks": {},
        "strategies": {
            "rules_only": {"model_invocations": 0, "fallbacks": 0, "latency_ms": []},
            "ollama_only": {"model_invocations": 0, "failures": 0, "latency_ms": []},
            "hybrid": {"model_invocations": 0, "fallbacks": 0, "latency_ms": [], "routes": {}},
        },
        "limitations": [
            "Only category and needs_response are evaluated; priority taxonomy is not comparable across the current rules and Ollama schemas.",
            "Ollama-only failures are scored as INVALID_OR_MISSING and are not silently replaced by rules predictions.",
            "Hybrid predictions use the model suggestion when routed to Ollama and rules output otherwise; fallback uses rules output.",
            "Each unique email is evaluated once per strategy, even if it contributes labels to both tasks.",
            "Frozen test data is for final reporting, not threshold tuning.",
        ],
    }

    # Infer each distinct eligible email once per arm. Do not count task-level calls.
    predictions: dict[str, dict[str, Any]] = {}
    for key, row in eligible.items():
        email = _email(row)
        rules = dict(rules_classifier(email))
        rules_preds = {task: _rules_prediction(rules, task) for task in TASKS}

        # Rules-only is deterministic and never invokes Ollama.
        predictions[key] = {"rules": rules_preds}

        # Ollama-only is an independent model baseline: always attempt one inference.
        try:
            started = time.perf_counter()
            model_result = _classification_dict(ollama_classifier({
                "sender": row.get("sender", ""), "subject": row.get("subject", ""),
                "snippet": row.get("snippet", ""), "body_text": row.get("body_text", ""),
            }, ollama_config))
            elapsed = float(model_result.get("latency_ms", (time.perf_counter() - started) * 1000))
            predictions[key]["ollama"] = model_result
            arm = report["strategies"]["ollama_only"]
            arm["model_invocations"] += 1
            arm["latency_ms"].append(elapsed)
        except OllamaError as exc:
            predictions[key]["ollama"] = None
            arm = report["strategies"]["ollama_only"]
            arm["model_invocations"] += 1
            arm["failures"] += 1
            arm.setdefault("failure_types", {})[type(exc).__name__] = arm.setdefault("failure_types", {}).get(type(exc).__name__, 0) + 1

        # Hybrid is one routing decision per email, not one per task.
        confidence = rules.get("confidence_score")
        try:
            confidence = float(confidence)
            confidence_valid = math.isfinite(confidence) and 0 <= confidence <= 100
        except (TypeError, ValueError):
            confidence_valid = False
            confidence = None
        hybrid_arm = report["strategies"]["hybrid"]
        if not enabled:
            route = "rules_only_disabled"
            hybrid_result = None
        elif confidence_valid and confidence >= threshold:
            route = "rules_confident"
            hybrid_result = None
        else:
            try:
                started = time.perf_counter()
                hybrid_result = _classification_dict(ollama_classifier({
                    "sender": row.get("sender", ""), "subject": row.get("subject", ""),
                    "snippet": row.get("snippet", ""), "body_text": row.get("body_text", ""),
                }, ollama_config))
                elapsed = float(hybrid_result.get("latency_ms", (time.perf_counter() - started) * 1000))
                hybrid_arm["model_invocations"] += 1
                hybrid_arm["latency_ms"].append(elapsed)
                route = "rules_plus_ollama_advisory"
            except OllamaError as exc:
                hybrid_result = None
                hybrid_arm["model_invocations"] += 1
                hybrid_arm["fallbacks"] += 1
                hybrid_arm.setdefault("failure_types", {})[type(exc).__name__] = hybrid_arm.setdefault("failure_types", {}).get(type(exc).__name__, 0) + 1
                route = "rules_fallback"
        hybrid_arm["routes"][route] = hybrid_arm["routes"].get(route, 0) + 1
        predictions[key]["hybrid"] = {
            task: _model_prediction(hybrid_result, task) if hybrid_result is not None else rules_preds[task]
            for task in TASKS
        }

    for task, (_, labels) in TASKS.items():
        pairs = task_rows.get(task, [])
        if not pairs:
            report["tasks"][task] = {"status": "empty_or_missing_frozen_test", "samples": 0}
            continue
        truth = [actual for _, actual in pairs]
        report["tasks"][task] = {
            "status": "frozen_holdout_evaluation",
            "frozen_test_rows_scored": len(pairs),
            "rules_only": score(truth, [predictions[k]["rules"][task] for k, _ in pairs], labels),
            "ollama_only": score(truth, [_model_prediction(predictions[k]["ollama"], task) for k, _ in pairs], labels),
            "hybrid": score(truth, [predictions[k]["hybrid"][task] for k, _ in pairs], labels),
        }

    for arm_name, arm in report["strategies"].items():
        latencies = arm.pop("latency_ms")
        arm["mean_latency_ms"] = round(statistics.mean(latencies), 2) if latencies else None
        arm["p95_latency_ms"] = (
            round(sorted(latencies)[min(len(latencies) - 1, math.ceil(.95 * len(latencies)) - 1)], 2)
            if latencies else None
        )
        arm["latency_samples"] = len(latencies)
        arm["invocation_rate"] = round(arm["model_invocations"] / len(eligible), 4) if eligible else 0.0
        if "fallbacks" in arm:
            arm["fallback_rate"] = round(arm["fallbacks"] / arm["model_invocations"], 4) if arm["model_invocations"] else 0.0
        else:
            arm["failure_rate"] = round(arm["failures"] / arm["model_invocations"], 4) if arm["model_invocations"] else 0.0
    # Explicit denominator: distinct emails eligible in at least one task's frozen holdout.
    report["evaluation_population"] = {"unique_emails": len(eligible)}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    lines = [
        "# FRIDAY Phase 4C — Controlled classifier comparison", "",
        f"- Model: `{ollama_config.model}`", f"- Hybrid threshold: `{threshold}`",
        f"- Unique eligible emails: `{len(eligible)}`",
        "- Ground truth: human-approved labels on existing frozen test assignments only.",
        "- Reports contain aggregate metrics only.", "",
    ]
    for task, item in report["tasks"].items():
        lines += [f"## {task}", f"- Status: `{item['status']}`"]
        for arm in ("rules_only", "ollama_only", "hybrid"):
            if arm in item:
                lines.append(f"- {arm}: accuracy `{item[arm]['accuracy']}`, macro-F1 `{item[arm]['macro_f1']}`, coverage `{item[arm]['prediction_coverage']}`")
        lines.append("")
    lines += ["## Invocation and latency"]
    for name, arm in report["strategies"].items():
        extra = (
            f"failures `{arm['failures']}`, failure rate `{arm['failure_rate']}`"
            if "failures" in arm else
            f"fallbacks `{arm['fallbacks']}`, fallback rate `{arm['fallback_rate']}`"
        )
        lines.append(
            f"- {name}: invocations `{arm['model_invocations']}`, "
            f"invocation rate `{arm['invocation_rate']}`, {extra}, "
            f"mean latency ms `{arm['mean_latency_ms']}`, p95 latency ms `{arm['p95_latency_ms']}`"
        )
    lines += ["", "## Limitations"] + ["- " + item for item in report["limitations"]]
    output_path.with_suffix(".md").write_text("\n".join(lines), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("data/email_labels_evaluation.csv"))
    parser.add_argument("--manifest", type=Path, default=Path("data/priority_engine_split.json"))
    parser.add_argument("--output", type=Path, default=Path("reports/phase4c_evaluation.json"))
    parser.add_argument("--threshold", type=float, default=70.0)
    parser.add_argument("--model", default=None)
    parser.add_argument("--disabled", action="store_true", help="Disable Ollama calls in hybrid arm; Ollama-only is still measured unless --rules-only")
    args = parser.parse_args()
    config = OllamaConfig.from_env()
    if args.model:
        config = OllamaConfig(
            host=config.host, model=args.model, timeout_seconds=config.timeout_seconds,
            max_body_chars=config.max_body_chars, num_ctx=config.num_ctx, temperature=config.temperature,
        )
    config_for_run, classifier = config, classify_with_ollama
    result = evaluate(args.input, args.manifest, args.output, args.threshold, config_for_run,
                      enabled=not args.disabled, ollama_classifier=classifier)
    summary = {
        "output": str(args.output),
        "tasks": {k: {"status": v["status"], "samples": v.get("frozen_test_rows_scored")} for k, v in result["tasks"].items()},
        "strategies": {k: {field: v.get(field) for field in ("model_invocations", "invocation_rate", "failures", "failure_rate", "fallbacks", "fallback_rate", "mean_latency_ms", "p95_latency_ms")} for k, v in result["strategies"].items()},
        "unique_emails": result["evaluation_population"]["unique_emails"],
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
