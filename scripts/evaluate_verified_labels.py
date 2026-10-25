"""Evaluate stored rules/selected predictions against verified labels.

Legacy-compatible entry point using the same normalization, frozen split manifest,
and invalid-prediction accounting as evaluate_priority_engine.py. No model/Gmail calls.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from scripts.evaluate_priority_engine import TASKS, _norm, metrics
from scripts.priority_engine_splits import load_or_create_test_ids, row_key


def evaluate_verified(input_path: Path, *, output_path: Path, test_fraction: float = 0.25,
                      seed: int = 42, min_labels: int = 20,
                      split_manifest: Path | None = None) -> dict[str, Any]:
    with input_path.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    manifest = split_manifest or input_path.parent / "priority_engine_split.json"
    report: dict[str, Any] = {
        "input_file_name": input_path.name,
        "split_manifest_file_name": manifest.name,
        "ground_truth_policy": "Only non-empty valid true_* human labels are evaluated; predictions are never ground truth.",
        "privacy_note": "Aggregate metrics only. The split manifest contains private identifiers and must remain git-ignored.",
        "tasks": {},
    }
    for task, config in TASKS.items():
        labels = config["labels"]
        valid = {label.lower() if task == "needs_response" else label for label in labels}
        verified = []
        invalid_truth = 0
        for row in rows:
            truth = _norm(row.get(config["truth"]), task)
            if not truth:
                continue
            if truth not in valid:
                invalid_truth += 1
                continue
            verified.append({**row, "_truth": truth, "_row_key": row_key(row)})
        item: dict[str, Any] = {"verified_labels": len(verified), "invalid_truth_rows": invalid_truth, "minimum_for_non_exploratory": min_labels}
        if len(verified) < 2:
            item.update({"status": "insufficient_verified_labels", "message": "Collect more human-verified labels before evaluating."})
            report["tasks"][task] = item
            continue
        try:
            test_ids, created = load_or_create_test_ids(
                rows, task=task, truth_column=config["truth"], labels=labels,
                manifest_path=manifest, test_fraction=test_fraction, seed=seed,
            )
        except (ImportError, ValueError) as exc:
            item.update({"status": "split_error", "message": str(exc)})
            report["tasks"][task] = item
            continue
        test = [row for row in verified if row["_row_key"] in test_ids]
        if not test:
            item.update({"status": "empty_frozen_test", "message": "No verified rows match the frozen test IDs. Do not silently recreate the manifest."})
            report["tasks"][task] = item
            continue
        truth = [row["_truth"] for row in test]
        normalized_labels = tuple(label.lower() for label in labels) if task == "needs_response" else labels
        def score_column(column: str) -> dict[str, Any]:
            return metrics(truth, [_norm(row.get(column), task) for row in test], normalized_labels)
        item.update({
            "status": "holdout_evaluation" if len(verified) >= min_labels else "exploratory",
            "frozen_test_rows": len(test),
            "frozen_test_manifest_created_this_run": created,
            "rules": score_column(config["rules"]),
            "selected_predictions": score_column(config["selected"]),
            "ollama_predictions": score_column(config.get("ollama", "")),
            "note": "Invalid or missing predictions count as errors and reduce coverage. Do not tune against this frozen test set.",
        })
        report["tasks"][task] = item
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    md = ["# FRIDAY Verified-Label Evaluation", "", "Ground truth uses human-verified `true_*` columns only.", "Invalid/missing predictions count as errors and reduce coverage.", "The private split manifest must remain git-ignored.", ""]
    for task, item in report["tasks"].items():
        md += [f"## {task}", f"- Verified labels: {item['verified_labels']}", f"- Status: {item['status']}"]
        if "rules" in item:
            for name in ("rules", "selected_predictions", "ollama_predictions"):
                score = item[name]
                md.append(f"- {name}: macro-F1 `{score['macro_f1']}`, accuracy `{score['accuracy']}`, coverage `{score['prediction_coverage']}`")
        elif "message" in item:
            md.append(f"- Note: {item['message']}")
        md.append("")
    output_path.with_suffix(".md").write_text("\n".join(md), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("data/email_labels.csv"))
    parser.add_argument("--output", type=Path, default=Path("reports/verified_label_evaluation.json"))
    parser.add_argument("--split-manifest", type=Path, default=Path("data/priority_engine_split.json"))
    parser.add_argument("--test-fraction", type=float, default=0.25)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--min-labels", type=int, default=20)
    args = parser.parse_args()
    if not 0.1 <= args.test_fraction <= 0.4:
        parser.error("--test-fraction must be between 0.1 and 0.4")
    if not args.input.is_file():
        parser.error(f"Input not found: {args.input}")
    try:
        report = evaluate_verified(args.input, output_path=args.output, test_fraction=args.test_fraction,
                                  seed=args.seed, min_labels=args.min_labels, split_manifest=args.split_manifest)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps({task: {"verified_labels": result["verified_labels"], "status": result["status"]} for task, result in report["tasks"].items()}, indent=2))
    print(f"Reports written to {args.output} and {args.output.with_suffix('.md')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
