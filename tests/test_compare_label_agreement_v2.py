from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from scripts.compare_label_agreement_v2 import AgreementError, compare_labels


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def old_row(key: str, spam: str, response: str, priority: str = "INFORMATIONAL") -> dict[str, str]:
    return {
        "email_id": key, "true_is_spam": spam, "true_needs_response": response,
        "true_priority": priority, "true_category": "JOB",
    }


def new_row(key: str, spam: str, response: str, priority: str = "INFORMATIONAL", note: str = "") -> dict[str, str]:
    return {
        "email_id": key, "is_spam": spam, "needs_response": response,
        "priority": priority, "category": "JOB", "note": note,
    }


def test_degenerate_one_class_rater_is_explicit_and_reports_prevalence(tmp_path: Path) -> None:
    old, new = tmp_path / "old.csv", tmp_path / "new.csv"
    write_csv(old, [old_row(str(i), "yes" if i < 5 else "no", "no") for i in range(20)])
    write_csv(new, [new_row(str(i), "no", "no") for i in range(20)])
    report = compare_labels(old, new)
    spam = report["tasks"]["is_spam"]
    assert spam["kappa"] == "undefined_degenerate"
    assert spam["raw_agreement"] == 0.75
    assert spam["prevalence"]["legacy"] == {"no": 0.75, "yes": 0.25}
    assert spam["prevalence"]["v2"] == {"no": 1.0, "yes": 0.0}
    assert "only one class" in spam["kappa_message"]


def test_binary_tasks_include_pabak_and_bootstrap_interval(tmp_path: Path) -> None:
    old, new = tmp_path / "old.csv", tmp_path / "new.csv"
    write_csv(old, [old_row(str(i), "yes" if i % 2 else "no", "yes" if i % 3 else "no") for i in range(30)])
    write_csv(new, [new_row(str(i), "yes" if i % 2 else "no", "yes" if i % 3 else "no") for i in range(30)])
    report = compare_labels(old, new, bootstrap_iterations=300)
    for task in ("is_spam", "needs_response"):
        assert report["tasks"][task]["pabak"] == 1.0
        assert report["tasks"][task]["pabak_bootstrap_95_interval"] == [1.0, 1.0]
        assert set(report["tasks"][task]["per_class_agreement"]) == {"no", "yes"}


def test_per_class_agreement_reports_each_class(tmp_path: Path) -> None:
    old, new = tmp_path / "old.csv", tmp_path / "new.csv"
    write_csv(old, [old_row("1", "yes", "no"), old_row("2", "no", "no")])
    write_csv(new, [new_row("1", "yes", "no"), new_row("2", "yes", "no")])
    report = compare_labels(old, new)
    yes = report["tasks"]["is_spam"]["per_class_agreement"]["yes"]
    assert yes["both_raters_label_count"] == 1
    assert yes["legacy_count"] == 1
    assert yes["v2_count"] == 2


def test_ai_draft_rows_refused_by_default_and_flagged_when_allowed(tmp_path: Path) -> None:
    old, new = tmp_path / "old.csv", tmp_path / "new.csv"
    write_csv(old, [old_row(str(i), "no", "no") for i in range(20)])
    rows = [new_row(str(i), "no", "no", note="Claude_Draft: generated suggestion" if i == 0 else "") for i in range(20)]
    write_csv(new, rows)
    with pytest.raises(AgreementError, match="AI-draft rows"):
        compare_labels(old, new)
    report = compare_labels(old, new, allow_draft_comparison=True, bootstrap_iterations=200)
    assert report["report_status"] == "NOT_A_SELF_CONSISTENCY_MEASURE"
    assert report["ai_draft_rows_detected"] is True


def test_no_draft_comparison_is_reported_as_human_annotation_comparison(tmp_path: Path) -> None:
    old, new = tmp_path / "old.csv", tmp_path / "new.csv"
    write_csv(old, [old_row(str(i), "no", "no") for i in range(2)])
    write_csv(new, [new_row(str(i), "no", "no") for i in range(2)])
    report = compare_labels(old, new)
    assert report["report_status"] == "HUMAN_ANNOTATION_COMPARISON"
    assert report["tasks"]["is_spam"]["pabak_bootstrap_95_interval"] is None


def test_report_never_contains_email_ids(tmp_path: Path) -> None:
    old, new = tmp_path / "old.csv", tmp_path / "new.csv"
    write_csv(old, [old_row("private-id", "no", "no")])
    write_csv(new, [new_row("private-id", "no", "no")])
    serialized = json.dumps(compare_labels(old, new))
    assert "private-id" not in serialized
    assert "email_id" not in serialized
