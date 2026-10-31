"""Dataset audit: counts, imbalance, integrity, provenance, and privacy."""
import csv
import json

import pytest

from scripts.audit_priority_dataset import AuditError, audit_dataset, main, render_dataset_card

SECRET = "Very Private Sender <very.private@secret-example.net>"


def _write_csv(path, rows, fields=None):
    fields = fields or list(rows[0])
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _row(i, **truth):
    base = {"email_id": f"id{i:03d}", "sender": SECRET, "subject": "private subject", "snippet": "", "body_text": "private body",
            "labels": "INBOX", "true_priority": "", "true_is_spam": "", "true_needs_response": "", "true_category": "",
            "label_source": "rules_confident"}
    base.update(truth)
    return base


@pytest.fixture()
def rows():
    data = []
    for i in range(10):
        data.append(_row(i, true_priority="INFORMATIONAL", true_is_spam="no", true_needs_response="no", true_category="UPDATES"))
    for i in range(10, 13):
        data.append(_row(i, true_priority="URGENT", true_is_spam="no", true_needs_response="yes", true_category="FINANCE"))
    data.append(_row(13, true_priority="NEEDS_REPLY", true_is_spam="no", true_needs_response="yes", true_category="PERSONAL"))
    data.append(_row(14, true_priority="SPAM", true_is_spam="yes", true_needs_response="no", true_category="PROMOTIONS"))
    data.append(_row(15, true_priority="bogus", true_is_spam="maybe", true_category="WRONG"))
    data.append(_row(16))  # unlabeled
    return data


def test_counts_distribution_and_imbalance(tmp_path, rows):
    path = tmp_path / "d.csv"
    _write_csv(path, rows)
    audit = audit_dataset(path, min_per_class=5, target_per_class=8)
    priority = audit["tasks"]["priority"]
    assert priority["verified_labels"] == 14  # SPAM (legacy) and invalid rows excluded
    assert priority["class_distribution"] == {"URGENT": 3, "NEEDS_REPLY": 1, "INFORMATIONAL": 10}
    assert priority["invalid_truth_values"] == 1
    assert priority["imbalance_ratio_max_to_min"] == 10.0
    assert set(priority["classes_below_minimum"]) == {"URGENT", "NEEDS_REPLY"}
    assert priority["labels_needed_to_reach_target"] == {"URGENT": 5, "NEEDS_REPLY": 7}
    category = audit["tasks"]["category"]
    assert category["classes_missing"] == ["JOB", "OTHER"]
    assert category["class_distribution"]["FINANCE"] == 3
    assert audit["tasks"]["is_spam"]["class_distribution"] == {"yes": 1, "no": 14}


def test_integrity_checks(tmp_path, rows):
    rows.append(_row(5, true_priority="URGENT"))  # duplicate id
    rows.append({**_row(99), "email_id": ""})  # missing id
    rows.append(_row(98, true_priority="SPAM", true_is_spam="no"))  # conflict
    path = tmp_path / "d.csv"
    _write_csv(path, rows)
    integrity = audit_dataset(path)["integrity"]
    assert integrity["duplicate_email_ids"] == 1 and integrity["missing_email_ids"] == 1
    assert integrity["priority_spam_conflicts"] == 1
    assert integrity["legacy_spam_in_true_priority"] == 2
    assert integrity["fully_unlabeled_rows"] >= 2


def test_missing_truth_columns_are_reported(tmp_path):
    path = tmp_path / "old.csv"
    _write_csv(path, [{"email_id": "a", "sender": "x", "true_priority": "URGENT"}])
    audit = audit_dataset(path)
    assert "true_is_spam" in audit["integrity"]["missing_truth_columns"]
    assert audit["tasks"]["is_spam"]["verified_labels"] == 0


def test_split_and_provenance_use_manifest_and_adjudication(tmp_path, rows):
    path = tmp_path / "d.csv"
    _write_csv(path, rows)
    manifest = tmp_path / "split.json"
    manifest.write_text(json.dumps({"version": 1, "tasks": {"category": {"test_ids": ["id000", "id010"]}}}), encoding="utf-8")
    before = manifest.read_bytes()
    adjudication = tmp_path / "adj.csv"
    _write_csv(adjudication, [{"email_id": "id001"}, {"email_id": "id010"}, {"email_id": "id999"}])
    audit = audit_dataset(path, manifest_path=manifest, adjudication_path=adjudication)
    category = audit["tasks"]["category"]
    assert category["split"]["frozen_test"] == 2 and category["split"]["development"] == category["verified_labels"] - 2
    assert category["from_final_adjudication"] == 2
    assert audit["tasks"]["priority"]["split"] == "no_frozen_split"
    assert audit["adjudication_file"] == "read"
    assert manifest.read_bytes() == before  # read-only
    assert audit_dataset(path)["adjudication_file"] == "not_provided"


def test_fingerprint_changes_when_the_dataset_changes(tmp_path, rows):
    path = tmp_path / "d.csv"
    _write_csv(path, rows)
    first = audit_dataset(path)["canonical_dataset"]["sha256_prefix"]
    _write_csv(path, rows[:-1])
    assert audit_dataset(path)["canonical_dataset"]["sha256_prefix"] != first


def test_audit_and_dataset_card_contain_no_email_content_or_ids(tmp_path, rows):
    path = tmp_path / "d.csv"
    _write_csv(path, rows)
    audit = audit_dataset(path)
    text = json.dumps(audit) + render_dataset_card(audit)
    for secret in (SECRET, "very.private", "secret-example", "private subject", "private body", "id001", "id010"):
        assert secret not in text
    card = render_dataset_card(audit)
    assert "Canonical dataset" in card and "Class-imbalance handling" in card and "Verified labels per task" in card


def test_cli_writes_json_and_card_and_reports_errors(tmp_path, rows, capsys):
    path = tmp_path / "d.csv"
    _write_csv(path, rows)
    out, doc = tmp_path / "r" / "audit.json", tmp_path / "docs" / "card.md"
    assert main(["--input", str(path), "--manifest", str(tmp_path / "none.json"), "--adjudication", str(tmp_path / "none.csv"),
                 "--output-json", str(out), "--write-doc", str(doc)]) == 0
    assert out.is_file() and doc.is_file()
    assert main(["--input", str(tmp_path / "missing.csv"), "--output-json", str(out)]) == 2
    with pytest.raises(AuditError):
        audit_dataset(tmp_path / "missing.csv")
    empty = tmp_path / "empty.csv"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(AuditError):
        audit_dataset(empty)
