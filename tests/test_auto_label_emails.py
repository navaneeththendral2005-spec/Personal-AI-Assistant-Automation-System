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
    assert reviewed == 1  # LLM failure is always queued, even if rules confidence is high
    assert _read_rows(tmp_path / "email_review_queue.csv")[0]["review_reason"].startswith("ollama_failure_rules_fallback:")
    assert "synthetic Ollama unavailable" in _read_rows(tmp_path / "email_review_queue.csv")[0]["review_reason"]


def test_successful_ollama_result_is_cached_across_runs(tmp_path):
    source = tmp_path / "email_labels.csv"
    _write_input(source)
    cache = tmp_path / "cache.sqlite3"
    calls = {"count": 0}

    def counted_llm(row, *, model, timeout):
        calls["count"] += 1
        return _llm_agree(row, model=model, timeout=timeout)

    kwargs = dict(use_llm=True, review_sample_rate=0.0, rules_classifier=_rules,
                  llm_classifier=counted_llm, cache_path=cache, progress=False)
    run_auto_labeling(source, **kwargs)
    assert calls["count"] == 1
    run_auto_labeling(source, **kwargs)
    assert calls["count"] == 1
    row = _read_rows(source)[0]
    assert row["label_source"] == "rules+ollama_cached"


def test_cache_invalidates_when_content_changes(tmp_path):
    source = tmp_path / "email_labels.csv"
    _write_input(source)
    cache = tmp_path / "cache.sqlite3"
    calls = {"count": 0}

    def counted_llm(row, *, model, timeout):
        calls["count"] += 1
        return _llm_agree(row, model=model, timeout=timeout)

    kwargs = dict(use_llm=True, review_sample_rate=0.0, rules_classifier=_rules,
                  llm_classifier=counted_llm, cache_path=cache, progress=False)
    run_auto_labeling(source, **kwargs)
    rows = _read_rows(source)
    rows[0]["subject"] = "Changed synthetic subject"
    with source.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    run_auto_labeling(source, **kwargs)
    assert calls["count"] == 2


def test_existing_verified_labels_are_never_overwritten(tmp_path):
    source = tmp_path / "email_labels.csv"
    _write_input(source)
    rows = _read_rows(source)
    rows[0].update({
        "true_priority": "URGENT",
        "true_needs_response": "yes",
        "true_category": "JOB",
    })
    with source.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    run_auto_labeling(source, use_llm=False, review_sample_rate=0.0, rules_classifier=_rules)
    row = _read_rows(source)[0]
    assert (row["true_priority"], row["true_needs_response"], row["true_category"]) == (
        "URGENT", "yes", "JOB"
    )


def test_corrupt_cache_entry_is_ignored_and_recomputed(tmp_path):
    import sqlite3
    from scripts.auto_label_emails import OllamaResultCache

    cache_path = tmp_path / "cache.sqlite3"
    cache = OllamaResultCache(cache_path)
    cache.put("test-key", _llm_agree({}, model="test", timeout=1)[0], 12.5)
    with sqlite3.connect(cache_path) as conn:
        conn.execute("UPDATE ollama_cache SET result_json=? WHERE cache_key=?", ('{"priority":"INVALID"}', "test-key"))

    assert cache.get("test-key") is None
    with sqlite3.connect(cache_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM ollama_cache WHERE cache_key=?", ("test-key",)).fetchone()[0] == 0


def test_ollama_predictions_are_stored_separately_from_rules_and_selected(tmp_path):
    source = tmp_path / "email_labels.csv"
    _write_input(source)
    run_auto_labeling(
        source, use_llm=True, review_sample_rate=0.0,
        rules_classifier=_rules, llm_classifier=_llm_disagree,
    )
    row = _read_rows(source)[0]
    assert row["rules_priority"] == "INFORMATIONAL"
    assert row["llm_priority"] == "NEEDS_REPLY"
    assert row["predicted_priority"] == row["llm_priority"]
    assert row["llm_status"] == "success"
    assert row["llm_reason"] == "Synthetic test disagreement"


def test_no_llm_run_is_marked_rules_only_not_fallback(tmp_path):
    source = tmp_path / "email_labels.csv"
    _write_input(source)
    run_auto_labeling(source, use_llm=False, review_sample_rate=0.0, rules_classifier=_rules)
    row = _read_rows(source)[0]
    assert row["label_source"] == "rules_only"
    assert row["llm_status"] == "disabled"
    assert row["llm_priority"] == ""



def test_hybrid_mode_skips_ollama_for_confident_rules(tmp_path):
    source = tmp_path / "email_labels.csv"
    _write_input(source)
    calls = {"count": 0}

    def should_not_run(*_args, **_kwargs):
        calls["count"] += 1
        raise AssertionError("Ollama should be skipped for confident rules")

    run_auto_labeling(
        source, use_llm=True, llm_only_when_low_confidence=True,
        confidence_threshold=0.65, review_sample_rate=0.0, progress=False,
        rules_classifier=_rules, llm_classifier=should_not_run,
    )
    row = _read_rows(source)[0]
    assert calls["count"] == 0
    assert row["label_source"] == "rules_confident"
    assert row["llm_status"] == "skipped_confident_rules"
    assert row["predicted_priority"] == "INFORMATIONAL"
