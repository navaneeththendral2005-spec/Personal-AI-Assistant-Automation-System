import csv
import json

import pytest

from backend.services.ollama_classifier import OllamaConfig, OllamaUnavailableError
from scripts.phase4c_evaluate_hybrid import evaluate


def _fixture(tmp_path):
    rows = [
        {"email_id": "a", "sender": "synthetic-a", "subject": "A", "snippet": "x",
         "body_text": "x", "labels": "", "true_category": "JOB", "true_needs_response": "yes"},
        {"email_id": "b", "sender": "synthetic-b", "subject": "B", "snippet": "y",
         "body_text": "y", "labels": "", "true_category": "OTHER", "true_needs_response": "no"},
    ]
    csv_path = tmp_path / "labels.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps({"version": 1, "tasks": {
        "category": {"test_ids": ["a", "b"]},
        "needs_response": {"test_ids": ["a", "b"]},
    }}))
    return csv_path, manifest_path


def test_three_arms_count_one_call_per_unique_email_per_model_arm(tmp_path):
    csv_path, manifest_path = _fixture(tmp_path)
    calls = []

    def rules(email):
        return {"category": "JOB", "requires_response": True, "confidence_score": 10}

    def ollama(row, config):
        calls.append(row["sender"])
        return {
            "category": "JOB" if row["sender"] == "synthetic-a" else "OTHER",
            "needs_response": "yes" if row["sender"] == "synthetic-a" else "no",
            "latency_ms": 12.5,
        }

    report = evaluate(csv_path, manifest_path, tmp_path / "out.json", 70,
                      OllamaConfig(), rules_classifier=rules, ollama_classifier=ollama)

    # Two independent arms, each one inference per unique email (not per task).
    assert len(calls) == 4
    assert report["evaluation_population"]["unique_emails"] == 2
    assert report["strategies"]["ollama_only"]["model_invocations"] == 2
    assert report["strategies"]["hybrid"]["model_invocations"] == 2
    assert report["strategies"]["hybrid"]["routes"] == {"rules_plus_ollama_advisory": 2}
    assert report["tasks"]["category"]["ollama_only"]["accuracy"] == 1.0
    assert report["tasks"]["needs_response"]["hybrid"]["accuracy"] == 1.0
    assert report["strategies"]["hybrid"]["mean_latency_ms"] == 12.5
    raw = (tmp_path / "out.json").read_text()
    assert "synthetic-a" not in raw and '"email_id"' not in raw


def test_ollama_only_failure_is_not_replaced_but_hybrid_falls_back(tmp_path):
    csv_path, manifest_path = _fixture(tmp_path)

    def rules(email):
        return {"category": "JOB", "requires_response": True, "confidence_score": 1}

    def failing(row, config):
        raise OllamaUnavailableError("offline")

    report = evaluate(csv_path, manifest_path, tmp_path / "out.json", 70,
                      OllamaConfig(), rules_classifier=rules, ollama_classifier=failing)
    assert report["tasks"]["category"]["ollama_only"]["prediction_coverage"] == 0.0
    assert report["tasks"]["category"]["hybrid"]["accuracy"] == 0.5
    assert report["strategies"]["ollama_only"]["failures"] == 2
    assert report["strategies"]["hybrid"]["fallbacks"] == 2
    assert report["strategies"]["hybrid"]["routes"] == {"rules_fallback": 2}


def test_confident_rules_skip_hybrid_but_not_ollama_baseline(tmp_path):
    csv_path, manifest_path = _fixture(tmp_path)
    calls = []

    def rules(email):
        return {"category": "JOB", "requires_response": True, "confidence_score": 99}

    def ollama(row, config):
        calls.append(row["sender"])
        return {"category": "OTHER", "needs_response": "no", "latency_ms": 3}

    report = evaluate(csv_path, manifest_path, tmp_path / "out.json", 70,
                      OllamaConfig(), rules_classifier=rules, ollama_classifier=ollama)
    assert report["strategies"]["ollama_only"]["model_invocations"] == 2
    assert report["strategies"]["hybrid"]["model_invocations"] == 0
    assert report["strategies"]["hybrid"]["routes"] == {"rules_confident": 2}
    assert len(calls) == 2


def test_rules_only_hybrid_disabled_has_zero_hybrid_calls(tmp_path):
    csv_path, manifest_path = _fixture(tmp_path)
    def rules(email):
        return {"category": "JOB", "requires_response": True, "confidence_score": 1}
    def ollama(row, config):
        return {"category": "JOB", "needs_response": "yes", "latency_ms": 2}
    report = evaluate(csv_path, manifest_path, tmp_path / "out.json", 70,
                      OllamaConfig(), enabled=False, rules_classifier=rules, ollama_classifier=ollama)
    assert report["strategies"]["hybrid"]["model_invocations"] == 0
    assert report["strategies"]["hybrid"]["routes"] == {"rules_only_disabled": 2}
