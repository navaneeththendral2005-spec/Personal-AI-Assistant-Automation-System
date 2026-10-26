from __future__ import annotations

import csv
import json
from pathlib import Path

from scripts.auto_label_emails import classify_with_rules
from scripts.evaluate_priority_engine import metrics
from scripts.evaluate_verified_labels import evaluate_verified


def test_rules_adapter_passes_real_email_text_to_production_classifier():
    result = classify_with_rules({
        "email_id": "synthetic-job-1",
        "sender": "recruiter@example.test",
        "subject": "Interview scheduled - please confirm your availability",
        "snippet": "Please reply today to confirm your interview.",
        "body_text": "Interview scheduled. Please reply today to confirm your availability.",
        "labels": "INBOX|UNREAD",
    })
    assert result["category"] == "JOB"
    assert result["priority"] == "URGENT"
    assert result["needs_response"] == "yes"
    assert result["confidence"] > 0
    assert result["is_spam"] == "no"


def test_rules_adapter_ignores_explicit_gmail_spam_label():
    from scripts.auto_label_emails import classify_with_rules

    result = classify_with_rules({
        "email_id": "synthetic-spam",
        "sender": "bulk@example.test",
        "subject": "Synthetic bulk email",
        "snippet": "Synthetic promotional content",
        "body_text": "Synthetic promotional content",
        "labels": "SPAM|INBOX",
    })

    assert result["is_spam"] == (
        "yes" if result["priority"] == "SPAM" else "no"
    )
    assert result["priority"] != "SPAM" or result["is_spam"] == "yes"


def test_invalid_and_missing_predictions_are_counted_as_errors():
    result = metrics(["URGENT", "SPAM", "INFORMATIONAL"], ["URGENT", "BROKEN", ""],
                     ("URGENT", "SPAM", "INFORMATIONAL"))
    assert result["samples"] == 3
    assert result["invalid_or_missing_predictions"] == 2
    assert abs(result["prediction_coverage"] - 1 / 3) < 0.0001
    assert abs(result["accuracy"] - 1 / 3) < 0.0001
    assert result["confusion_matrix_rows_actual_columns_predicted"]["SPAM"]["__INVALID_OR_MISSING__"] == 1


def _make_rows(path: Path):
    fields = ["email_id", "true_priority", "true_needs_response", "true_category",
              "rules_priority", "rules_needs_response", "rules_category",
              "predicted_priority", "predicted_needs_response", "predicted_category"]
    priorities = ["URGENT", "NEEDS_REPLY", "INFORMATIONAL", "SPAM"]
    categories = ["JOB", "FINANCE", "PERSONAL", "PROMOTIONS", "UPDATES", "OTHER"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for i in range(72):
            p, c, r = priorities[i % 4], categories[i % 6], "yes" if i % 2 else "no"
            writer.writerow({"email_id": f"synthetic-{i}", "true_priority": p,
                             "true_needs_response": r, "true_category": c,
                             "rules_priority": p, "rules_needs_response": r,
                             "rules_category": c, "predicted_priority": p,
                             "predicted_needs_response": r, "predicted_category": c})


def test_verified_evaluator_normalizes_case_and_uses_frozen_manifest(tmp_path):
    source = tmp_path / "labels.csv"
    manifest = tmp_path / "data" / "split.json"
    _make_rows(source)
    output = tmp_path / "reports" / "verified.json"
    first = evaluate_verified(source, output_path=output, split_manifest=manifest)
    assert first["tasks"]["priority"]["status"] == "holdout_evaluation"
    assert first["tasks"]["category"]["rules"]["accuracy"] == 1.0
    saved = json.loads(manifest.read_text(encoding="utf-8"))
    old_test_ids = set(saved["tasks"]["priority"]["test_ids"])
    # Add a newly verified row: it must not silently enter the frozen test set.
    with source.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["email_id", "true_priority", "true_needs_response", "true_category",
            "rules_priority", "rules_needs_response", "rules_category", "predicted_priority", "predicted_needs_response", "predicted_category"])
        writer.writerow({"email_id": "newly-labeled", "true_priority": "URGENT", "true_needs_response": "yes", "true_category": "JOB",
                         "rules_priority": "URGENT", "rules_needs_response": "yes", "rules_category": "JOB",
                         "predicted_priority": "URGENT", "predicted_needs_response": "yes", "predicted_category": "JOB"})
    second = evaluate_verified(source, output_path=output, split_manifest=manifest)
    saved_again = json.loads(manifest.read_text(encoding="utf-8"))
    assert set(saved_again["tasks"]["priority"]["test_ids"]) == old_test_ids
    assert "newly-labeled" not in old_test_ids
    assert second["tasks"]["priority"]["frozen_test_rows"] == first["tasks"]["priority"]["frozen_test_rows"]


def test_rules_adapter_ignores_gmail_labels_for_spam_classification():
    from scripts.auto_label_emails import classify_with_rules

    base_email = {
        "sender": "bulk@example.test",
        "subject": "Synthetic promotional message",
        "snippet": "Synthetic content",
        "body_text": "Synthetic content",
    }

    explicit_spam = classify_with_rules({
        **base_email,
        "email_id": "synthetic-spam",
        "labels": "SPAM|INBOX",
    })

    promotion_only = classify_with_rules({
        **base_email,
        "email_id": "synthetic-promo",
        "labels": "CATEGORY_PROMOTIONS|INBOX",
    })

    assert explicit_spam["priority"] == promotion_only["priority"]
    assert explicit_spam["is_spam"] == promotion_only["is_spam"]


def test_rules_adapter_maps_explicit_response_request_to_needs_reply():
    from scripts.auto_label_emails import classify_with_rules

    result = classify_with_rules({
        "email_id": "synthetic-reply", "sender": "person@example.test",
        "subject": "Please confirm", "snippet": "Please reply to confirm your availability.",
        "body_text": "Please reply to confirm your availability.", "labels": "INBOX|UNREAD",
    })
    assert result["needs_response"] == "yes"
    assert result["priority"] == "NEEDS_REPLY" or result["priority"] == "URGENT"

def test_production_classifier_detects_explicit_reply_request():
    from types import SimpleNamespace
    from backend.services.email_classifier import classify_email

    message = SimpleNamespace(
        sender="person@example.test",
        subject="Please confirm",
        snippet="Please reply to confirm your availability.",
        body_text="Please reply to confirm your availability.",
        labels=["INBOX", "UNREAD"],
    )

    result = classify_email(message)

    assert result["requires_response"] is True
    assert result["response_confidence"] > 0
    assert result["priority"] in {"NORMAL", "HIGH", "URGENT"}


def test_production_classifier_detects_urgent_payment_language():
    from types import SimpleNamespace
    from backend.services.email_classifier import classify_email

    message = SimpleNamespace(
        sender="billing@example.test",
        subject="Final notice: payment overdue",
        snippet="Immediate action required. Your payment is overdue.",
        body_text="Immediate action required. Your payment is overdue.",
        labels=["INBOX", "UNREAD"],
    )

    result = classify_email(message)

    assert result["priority"] == "URGENT"
    assert result["priority_score"] == 95


def test_production_classifier_keeps_routine_newsletter_low_priority():
    from types import SimpleNamespace
    from backend.services.email_classifier import classify_email

    message = SimpleNamespace(
        sender="newsletter@example.test",
        subject="Weekly newsletter",
        snippet="Read this week's product updates.",
        body_text="Here are the latest product updates and news.",
        labels=["INBOX"],
    )

    result = classify_email(message)

    assert result["priority"] == "LOW"