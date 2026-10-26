from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from scripts.evaluate_priority_engine_development import evaluate_development
from scripts.priority_engine_splits import row_key


def _write_dataset(path: Path) -> list[dict[str, str]]:
    rows = []
    for index in range(12):
        truth = "yes" if index % 2 == 0 else "no"
        rows.append({
            "email_id": f"synthetic-{index}",
            "sender": f"sender{index}@example.test",
            "subject": "Please confirm the meeting" if truth == "yes" else "Weekly newsletter",
            "snippet": "Could you reply today?" if truth == "yes" else "Routine update",
            "body_text": "Please confirm and respond" if truth == "yes" else "Monthly digest",
            "labels": "INBOX",
            "true_needs_response": truth,
            "rules_needs_response": truth,
            "predicted_needs_response": truth,
            "llm_needs_response": truth,
        })
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return rows


def test_development_evaluation_excludes_frozen_ids_and_never_rewrites_manifest(tmp_path):
    dataset = tmp_path / "labels.csv"
    rows = _write_dataset(dataset)
    manifest = tmp_path / "split.json"
    frozen_ids = [row_key(rows[0]), row_key(rows[1])]
    manifest.write_text(json.dumps({
        "version": 1,
        "tasks": {
            "needs_response": {
                "truth_column": "true_needs_response",
                "labels": ["yes", "no"],
                "test_ids": frozen_ids,
            }
        },
    }), encoding="utf-8")
    before = manifest.read_bytes()

    report = evaluate_development(dataset, manifest, seed=7, max_folds=3)

    result = report["tasks"]["needs_response"]
    assert result["development_rows"] == 10
    assert result["frozen_holdout_rows_excluded"] == 2
    assert result["folds"] == 3
    assert "rules" in result
    assert "word_tfidf_logistic_regression" in result["candidates"]
    assert manifest.read_bytes() == before
    assert "synthetic-0" not in json.dumps(report)
    assert "Please confirm" not in json.dumps(report)


def test_development_evaluation_refuses_to_create_missing_manifest(tmp_path):
    dataset = tmp_path / "labels.csv"
    _write_dataset(dataset)
    with pytest.raises(ValueError, match="will not create or repair"):
        evaluate_development(dataset, tmp_path / "missing.json")


def test_development_evaluation_fails_on_duplicate_source_keys(tmp_path):
    dataset = tmp_path / "labels.csv"
    rows = _write_dataset(dataset)
    rows[1]["email_id"] = rows[0]["email_id"]
    with dataset.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    manifest = tmp_path / "split.json"
    manifest.write_text(json.dumps({"version": 1, "tasks": {}}), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate row keys"):
        evaluate_development(dataset, manifest)
