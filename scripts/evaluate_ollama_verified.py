"""Run local Ollama on human-verified emails and score it without editing the source CSV.

The input is read-only. Only rows with at least one non-empty true_* label are
copied to a temporary file, Ollama predictions are generated there, and the
existing aggregate-only evaluator writes the final report. Email content and
identifiers are never written to the report.
"""
from __future__ import annotations

import argparse
import csv
import json
import tempfile
from pathlib import Path

from scripts.auto_label_emails import DEFAULT_CACHE_PATH, run_auto_labeling
from scripts.evaluate_verified_labels import evaluate_verified
from scripts.evaluate_priority_engine import TASKS


TRUTH_COLUMNS = tuple(config["truth"] for config in TASKS.values())


def run(input_path: Path, *, output_path: Path, split_manifest: Path,
        model: str = "qwen3:4b", timeout: float = 45.0,
        cache_path: Path = DEFAULT_CACHE_PATH, min_labels: int = 20,
        test_fraction: float = 0.25, seed: int = 42) -> dict:
    if not input_path.is_file():
        raise FileNotFoundError(f"Input CSV not found: {input_path}")
    with input_path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError("Input CSV has no header")
        missing = [column for column in TRUTH_COLUMNS if column not in reader.fieldnames]
        if missing:
            raise ValueError("Input CSV is missing human-label columns: " + ", ".join(missing))
        fieldnames = list(reader.fieldnames)
        verified_rows = [
            row for row in reader
            if any((row.get(column) or "").strip() for column in TRUTH_COLUMNS)
        ]
    if not verified_rows:
        raise ValueError("No human-verified true_* labels found; label emails before evaluation.")

    # Use a temporary workspace so the original dataset is never rewritten by auto-labeling.
    with tempfile.TemporaryDirectory(prefix="friday-ollama-eval-") as temporary:
        temporary_input = Path(temporary) / "verified_emails.csv"
        with temporary_input.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(verified_rows)

        total, _ = run_auto_labeling(
            temporary_input,
            use_llm=True,
            model=model,
            timeout=timeout,
            cache_path=cache_path,
            review_sample_rate=0.0,
            progress=True,
        )
        if total != len(verified_rows):
            raise RuntimeError("Unexpected row count after temporary auto-labeling.")
        report = evaluate_verified(
            temporary_input,
            output_path=output_path,
            test_fraction=test_fraction,
            seed=seed,
            min_labels=min_labels,
            split_manifest=split_manifest,
        )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("data/email_labels.csv"))
    parser.add_argument("--output", type=Path, default=Path("reports/ollama_verified_evaluation.json"))
    parser.add_argument("--split-manifest", type=Path, default=Path("data/priority_engine_split.json"))
    parser.add_argument("--model", default="qwen3:4b")
    parser.add_argument("--timeout", type=float, default=45.0)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE_PATH)
    parser.add_argument("--min-labels", type=int, default=20)
    parser.add_argument("--test-fraction", type=float, default=0.25)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    if not 0.1 <= args.test_fraction <= 0.4:
        parser.error("--test-fraction must be between 0.1 and 0.4")
    try:
        report = run(
            args.input, output_path=args.output, split_manifest=args.split_manifest,
            model=args.model, timeout=args.timeout, cache_path=args.cache,
            min_labels=args.min_labels, test_fraction=args.test_fraction, seed=args.seed,
        )
    except (OSError, ValueError, RuntimeError) as exc:
        parser.error(str(exc))
    print(json.dumps({
        task: {
            "verified_labels": item.get("verified_labels"),
            "status": item.get("status"),
            "frozen_test_rows": item.get("frozen_test_rows"),
            "ollama_accuracy": item.get("ollama_predictions", {}).get("accuracy"),
            "ollama_macro_f1": item.get("ollama_predictions", {}).get("macro_f1"),
            "ollama_coverage": item.get("ollama_predictions", {}).get("prediction_coverage"),
        }
        for task, item in report["tasks"].items()
    }, indent=2))
    print(f"Aggregate reports written to {args.output} and {args.output.with_suffix('.md')}")
    print("Original input CSV was not modified. Review reports as exploratory if sample sizes are small.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
