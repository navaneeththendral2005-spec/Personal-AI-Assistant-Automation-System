from __future__ import annotations

import csv
import io
from pathlib import Path

from scripts.evaluate_priority_engine import evaluate, metrics
from scripts.label_emails import label_dataset


def test_evaluator_refuses_to_claim_quality_with_insufficient_verified_labels(tmp_path):
    path = tmp_path / "labels.csv"
    path.write_text(
        "email_id,true_priority,true_needs_response,true_category,rules_priority,rules_needs_response,rules_category,predicted_priority,predicted_needs_response,predicted_category\n"
        "private-id-1,URGENT,yes,JOB,URGENT,yes,JOB,URGENT,yes,JOB\n"
        "private-id-2,,,,INFORMATIONAL,no,UPDATES,INFORMATIONAL,no,UPDATES\n",
        encoding="utf-8",
    )
    report = evaluate(path, min_verified=2)
    assert report["tasks"]["priority"]["verified_labels"] == 1
    assert report["tasks"]["priority"]["status"] == "insufficient_verified_data"
    assert report["tasks"]["needs_response"]["status"] == "insufficient_verified_data"
    assert "private-id-1" not in str(report)


def test_metrics_includes_class_support_and_confusion_matrix():
    result = metrics(["URGENT", "INFORMATIONAL"], ["URGENT", "URGENT"], ("URGENT", "INFORMATIONAL"))
    assert result["accuracy"] == 0.5
    assert result["per_class"]["INFORMATIONAL"]["support"] == 1
    assert result["confusion_matrix_rows_actual_columns_predicted"]["INFORMATIONAL"]["URGENT"] == 1


def test_labeler_can_limit_work_to_review_queue_and_shows_provisional_suggestions(tmp_path):
    dataset = tmp_path / "email_labels.csv"
    dataset.write_text(
        "email_id,sender,subject,snippet,body_text,predicted_priority,predicted_needs_response,predicted_category,true_priority,true_needs_response,true_category\n"
        "id-1,sender,test,brief,body,URGENT,yes,JOB,,,,\n"
        "id-2,sender,test2,brief,body,INFORMATIONAL,no,UPDATES,,,,\n",
        encoding="utf-8",
    )
    queue = tmp_path / "queue.csv"
    queue.write_text("email_id,review_reason\nid-2,disagreement\n", encoding="utf-8")
    answers = iter(["INFORMATIONAL", "no", "UPDATES"])
    output = io.StringIO()
    done, total = label_dataset(dataset, queue_path=queue, input_fn=lambda _p: next(answers), output=output)
    assert (done, total) == (1, 1)
    assert "Provisional suggestions" in output.getvalue()
    with dataset.open(newline="", encoding="utf-8-sig") as f:
        rows = {r["email_id"]: r for r in csv.DictReader(f)}
    assert rows["id-1"]["true_priority"] == ""
    assert rows["id-2"]["true_category"] == "UPDATES"


def test_evaluator_trains_tfidf_only_when_verified_data_is_sufficient(tmp_path):
    import importlib.util
    if importlib.util.find_spec("sklearn") is None:
        return
    path = tmp_path / "balanced.csv"
    fields = ["email_id", "sender", "subject", "snippet", "body_text", "labels",
              "true_priority", "true_needs_response", "true_category",
              "rules_priority", "rules_needs_response", "rules_category",
              "predicted_priority", "predicted_needs_response", "predicted_category"]
    priorities = ["URGENT", "NEEDS_REPLY", "INFORMATIONAL", "SPAM"]
    responses = ["yes", "no"]
    categories = ["JOB", "FINANCE", "PERSONAL", "PROMOTIONS", "UPDATES", "OTHER"]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for i in range(72):
            priority = priorities[i % len(priorities)]
            response = responses[i % len(responses)]
            category = categories[i % len(categories)]
            writer.writerow({
                "email_id": f"private-{i}", "sender": f"sender{i}@example.test",
                "subject": f"{category} message {i}", "snippet": f"synthetic {priority}",
                "body_text": "synthetic-only test content", "labels": "INBOX",
                "true_priority": priority, "true_needs_response": response, "true_category": category,
                "rules_priority": priority, "rules_needs_response": response, "rules_category": category,
                "predicted_priority": priority, "predicted_needs_response": response, "predicted_category": category,
            })
    report = evaluate(path, min_verified=30)
    for task in ("priority", "needs_response", "category"):
        assert report["tasks"][task]["status"] == "holdout_evaluation"
        assert report["tasks"][task]["tfidf_logistic_regression"]["samples"] > 0
    assert "private-0" not in str(report)

def test_frozen_holdout_does_not_expand_when_new_verified_rows_are_added(tmp_path):
    import csv

    from scripts.evaluate_priority_engine import TASKS
    from scripts.priority_engine_splits import (
        load_or_create_test_ids,
        row_key,
    )

    manifest = tmp_path / "priority_engine_split.json"
    labels = ("URGENT", "NEEDS_REPLY", "INFORMATIONAL", "SPAM")

    original_rows = [
        {
            "email_id": f"email-{i}",
            "true_priority": labels[i % len(labels)],
        }
        for i in range(40)
    ]

    original_test_ids, created = load_or_create_test_ids(
        original_rows,
        task="priority",
        truth_column=TASKS["priority"]["truth"],
        labels=labels,
        manifest_path=manifest,
        test_fraction=0.25,
        seed=42,
    )

    assert created
    assert original_test_ids

    expanded_rows = original_rows + [
        {
            "email_id": f"new-email-{i}",
            "true_priority": labels[i % len(labels)],
        }
        for i in range(40, 60)
    ]

    expanded_test_ids, created_again = load_or_create_test_ids(
        expanded_rows,
        task="priority",
        truth_column=TASKS["priority"]["truth"],
        labels=labels,
        manifest_path=manifest,
        test_fraction=0.25,
        seed=42,
    )

    assert not created_again
    assert expanded_test_ids == original_test_ids
    assert all(
        row_key(row) not in expanded_test_ids
        for row in expanded_rows
        if row["email_id"].startswith("new-email-")
    )