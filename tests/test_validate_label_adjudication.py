from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from scripts.validate_label_adjudication import run_validation, validate_rows

HEADERS = [
    "email_id", "final_true_is_spam", "final_true_priority",
    "final_true_needs_response", "final_true_category", "decision_reason",
]


def valid_row(email_id: str = "email-1", **overrides: str) -> dict[str, str]:
    row = {
        "email_id": email_id,
        "final_true_is_spam": "no",
        "final_true_priority": "NEEDS_REPLY",
        "final_true_needs_response": "yes",
        "final_true_category": "JOB",
        "decision_reason": "A response is expected.",
    }
    row.update(overrides)
    return row


def write_csv(path: Path, rows: list[dict[str, str]], headers: list[str] | None = None) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers or HEADERS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def test_valid_labels_pass_and_report_contains_no_email_content(tmp_path: Path) -> None:
    source = tmp_path / "labels.csv"
    report_path = tmp_path / "reports" / "result.json"
    write_csv(source, [valid_row()])

    report = run_validation(source, report_path)

    assert report["validation_status"] == "passed"
    assert report["reviewed_emails"] == 1
    assert "email-1" not in report_path.read_text(encoding="utf-8")
    assert json.loads(report_path.read_text(encoding="utf-8")) == report


def test_spam_priority_must_be_blank(tmp_path: Path) -> None:
    report = validate_rows([
        valid_row(final_true_is_spam="yes", final_true_priority="URGENT")
    ])
    assert report["validation_status"] == "failed"
    assert report["spam_priority_conflicts"] == 1


def test_spam_priority_may_be_blank(tmp_path: Path) -> None:
    report = validate_rows([
        valid_row(final_true_is_spam="yes", final_true_priority="")
    ])
    assert report["validation_status"] == "passed"


def test_missing_final_label_and_reason_fail() -> None:
    report = validate_rows([
        valid_row(final_true_category="BOGUS", final_true_needs_response="", decision_reason="")
    ])
    assert report["validation_status"] == "failed"
    assert report["invalid_values_by_field"]["final_true_category"] == 1
    assert report["missing_values_by_field"]["final_true_needs_response"] == 1
    assert report["missing_decision_reasons"] == 1


def test_duplicate_ids_are_rejected() -> None:
    with pytest.raises(ValueError, match="Duplicate email IDs"):
        validate_rows([valid_row(), valid_row()])


def test_missing_required_header_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "labels.csv"
    write_csv(source, [valid_row()], headers=HEADERS[:-1])
    with pytest.raises(ValueError, match="decision_reason"):
        run_validation(source, tmp_path / "report.json")


def test_missing_input_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="not found"):
        run_validation(tmp_path / "missing.csv", tmp_path / "report.json")
