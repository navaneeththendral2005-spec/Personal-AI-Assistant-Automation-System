from __future__ import annotations

import csv
import json
from pathlib import Path

from scripts.evaluate_priority_engine_advanced import bootstrap_intervals, evaluate_advanced


def test_bootstrap_intervals_are_deterministic_and_bounded():
    a = bootstrap_intervals(["yes", "no", "yes"], ["yes", "yes", "no"], ("yes", "no"), iterations=100, seed=7)
    b = bootstrap_intervals(["yes", "no", "yes"], ["yes", "yes", "no"], ("yes", "no"), iterations=100, seed=7)
    assert a == b
    assert 0 <= a["accuracy_95ci"]["lower"] <= a["accuracy_95ci"]["upper"] <= 1


def test_advanced_evaluation_keeps_small_data_as_insufficient_and_private(tmp_path):
    path = tmp_path / "labels.csv"
    path.write_text(
        "email_id,sender,subject,snippet,body_text,labels,true_priority,true_needs_response,true_category,rules_priority,rules_needs_response,rules_category,predicted_priority,predicted_needs_response,predicted_category\n"
        "private-id-1,a,b,c,d,INBOX,URGENT,yes,JOB,URGENT,yes,JOB,URGENT,yes,JOB\n"
        "private-id-2,a,b,c,d,INBOX,SPAM,no,OTHER,SPAM,no,OTHER,SPAM,no,OTHER\n",
        encoding="utf-8",
    )
    report = evaluate_advanced(path, split_manifest=tmp_path / "split.json", min_verified=30, bootstrap_iterations=100)
    assert report["tasks"]["priority"]["status"] == "insufficient_verified_data"
    assert "private-id-1" not in json.dumps(report)

def test_bootstrap_intervals_handle_perfect_predictions():
    result = bootstrap_intervals(
        ["URGENT", "SPAM", "INFORMATIONAL", "NEEDS_REPLY"],
        ["URGENT", "SPAM", "INFORMATIONAL", "NEEDS_REPLY"],
        ("URGENT", "SPAM", "INFORMATIONAL", "NEEDS_REPLY"),
        iterations=200,
        seed=42,
    )

    accuracy_ci = result["accuracy_95ci"]
    macro_f1_ci = result["macro_f1_95ci"]

    assert 0.0 <= accuracy_ci["lower"] <= accuracy_ci["upper"] <= 1.0
    assert 0.0 <= macro_f1_ci["lower"] <= macro_f1_ci["upper"] <= 1.0

    # Every original prediction is correct, so the point estimate is perfect.
    # Bootstrap intervals can still have lower bounds below 1.0.
    assert accuracy_ci["upper"] == 1.0
    assert macro_f1_ci["upper"] == 1.0