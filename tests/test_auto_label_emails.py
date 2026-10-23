"""Tests for provisional auto-labeling; no real database or Ollama is used."""
from __future__ import annotations

import csv
from pathlib import Path

from scripts.auto_label_emails import run_auto_labeling


def _rules(_row):
    return {
        "priority": "INFORMATIONAL", "needs_response": "no", "category": "UPDATES",
        "confidence": 0.8,
    }


def _llm_agree(_row, *, model, timeout):
    return ({
        "priority": "INFORMATIONAL", "needs_response": "no", "category": "UPDATES",
        "confidence": 0.9, "reason": "Synthetic test",
    }, 12.5)


def _llm_disagree(_row, *, model, timeout):
    return ({
        "priority": "NEEDS_REPLY", "needs_response": "yes", "category": "JOB",
        "confidence": 0.9, "reason": "Synthetic test disagreement",
    }, 13.0)


def _write_input(path: Path):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=[
            "email_id", "sender", "subject", "snippet", "body_text", "labels",
            "true_priority", "true_needs_response", "true_category",
        ])
        writer.writeheader()
        writer.writerow({
            "email_id": "synthetic-1", "sender": "example@example.test",
            "subject": "Synthetic notice", "snippet": "A sample notice",
            "body_text": "Synthetic body only", "labels": "INBOX",
            "true_priority": "", "true_needs_response": "", "true_category": "",
        })


def _read_rows(path: Path):
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def test_predictions_are_provisional_and_agreement_gets_sampled_review(tmp_path):
    source = tmp_path / "email_labels.csv"
    _write_input(source)
    total, reviewed = run_auto_labeling(
        source, use_llm=True, review_sample_rate=1.0, seed=1,
        rules_classifier=_rules, llm_classifier=_llm_agree,
    )
    rows = _read_rows(source)
    queue = _read_rows(tmp_path / "email_review_queue.csv")
    assert total == 1 and reviewed == 1
    assert rows[0]["predicted_priority"] == "INFORMATIONAL"
    assert rows[0]["label_source"] == "rules+ollama"
    assert rows[0]["needs_review"] == "yes"
    assert rows[0]["true_priority"] == ""
    assert queue[0]["email_id"] == "synthetic-1"
    assert queue[0]["review_reason"] == "random_quality_check_sample"


def test_model_disagreement_is_queued_for_review(tmp_path):
    source = tmp_path / "email_labels.csv"
    _write_input(source)
    run_auto_labeling(
        source, use_llm=True, review_sample_rate=0.0,
        rules_classifier=_rules, llm_classifier=_llm_disagree,
    )
    row = _read_rows(source)[0]
    assert row["predicted_priority"] == "NEEDS_REPLY"
    assert row["needs_review"] == "yes"
    assert "model_disagreement" in row["review_reason"]


def test_llm_failure_falls_back_to_rules_and_does_not_fill_true_labels(tmp_path):
    source = tmp_path / "email_labels.csv"
    _write_input(source)

    def broken_llm(*_args, **_kwargs):
        raise OSError("synthetic Ollama unavailable")

    total, reviewed = run_auto_labeling(
        source, use_llm=True, review_sample_rate=0.0,
        rules_classifier=_rules, llm_classifier=broken_llm,
    )
    row = _read_rows(source)[0]
    assert total == 1
    assert row["predicted_priority"] == "INFORMATIONAL"
    assert row["label_source"] == "rules_fallback"
    assert row["true_priority"] == ""
    assert row["true_needs_response"] == ""
    assert row["true_category"] == ""
    assert reviewed == 0  # rules confidence is above the threshold
