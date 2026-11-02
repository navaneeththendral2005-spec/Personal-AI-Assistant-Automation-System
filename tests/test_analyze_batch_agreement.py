from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from scripts.analyze_batch_agreement import analyze, cohen_kappa, write_reports

FIELDS = [
    "email_id", "batch_name", "guide_version", "is_spam",
    "needs_response", "priority", "category", "unsure", "note",
]


def row(eid, batch, guide, **overrides):
    values = {
        "email_id": eid, "batch_name": batch, "guide_version": guide,
        "is_spam": "no", "needs_response": "no", "priority": "INFORMATIONAL",
        "category": "UPDATES", "unsure": "no", "note": "",
    }
    values.update(overrides)
    return values


def write_csv(path: Path, rows) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def test_kappa_perfect_and_undefined():
    # Perfect agreement with multiple classes
    assert cohen_kappa(
        ["a", "b"],
        ["a", "b"]
    ) == 1.0

    # Single-class agreement: mathematically undefined
    assert cohen_kappa(
        ["a", "a"],
        ["a", "a"]
    ) is None

    # Completely different constant annotations
    assert cohen_kappa(
        ["a", "a"],
        ["b", "b"]
    ) == 0.0

    # Empty annotations
    assert cohen_kappa([], []) is None


def test_cross_version_pair_and_confusion_matrix_are_aggregate_only(tmp_path):
    source = tmp_path / "labels.csv"
    write_csv(source, [
        row("synthetic-1", "pilot", "v2.1", category="JOB"),
        row("synthetic-1", "batch", "v2.0-draft", category="JOB"),
        row("synthetic-2", "pilot", "v2.1", category="FINANCE"),
        row("synthetic-2", "batch", "v2.0-draft", category="PROMOTIONS"),
    ])
    report, matrices = analyze(source)
    pair = report["pairs"][0]
    assert pair["comparison_type"] == "cross_guide_version"
    assert pair["shared_email_count"] == 2
    # Batch names are sorted, so the first matrix axis is batch_21_60
    # (PROMOTIONS) and the second is the v2.1 pilot (FINANCE).
    assert pair["tasks"]["category"]["confusion_matrix_all_valid"]["PROMOTIONS"]["FINANCE"] == 1
    assert matrices
    assert "synthetic-1" not in json.dumps(report)


def test_unsure_is_excluded_from_confident_only(tmp_path):
    source = tmp_path / "labels.csv"
    write_csv(source, [
        row("a", "one", "v2", category="JOB", unsure="yes"),
        row("a", "two", "v2", category="FINANCE"),
        row("b", "one", "v2", category="JOB"),
        row("b", "two", "v2", category="JOB"),
    ])
    report, _ = analyze(source)
    result = report["pairs"][0]["tasks"]["category"]
    assert result["all_valid_annotations"]["paired_annotations"] == 2
    assert result["excluding_unsure_annotations"]["paired_annotations"] == 1
    assert result["excluded_unsure_pairs"] == 1


def test_priority_excludes_spam_marked_pairs(tmp_path):
    source = tmp_path / "labels.csv"
    write_csv(source, [
        row("a", "one", "v2", is_spam="yes", priority=""),
        row("a", "two", "v2", priority="URGENT"),
        row("b", "one", "v2", priority="URGENT"),
        row("b", "two", "v2", priority="URGENT"),
    ])
    report, _ = analyze(source)
    result = report["pairs"][0]["tasks"]["priority"]
    assert result["excluded_priority_not_applicable_pairs"] == 1
    assert result["all_valid_annotations"]["paired_annotations"] == 1


def test_no_overlap_is_not_reported(tmp_path):
    source = tmp_path / "labels.csv"
    write_csv(source, [row("a", "one", "v2"), row("b", "two", "v2")])
    report, matrices = analyze(source)
    assert report["pairs"] == []
    assert matrices == []


def test_mixed_guides_inside_batch_rejected(tmp_path):
    source = tmp_path / "labels.csv"
    write_csv(source, [row("a", "one", "v2"), row("b", "one", "v2.1")])
    with pytest.raises(ValueError, match="multiple guide versions"):
        analyze(source)


def test_duplicate_email_batch_rejected(tmp_path):
    source = tmp_path / "labels.csv"
    item = row("a", "one", "v2")
    write_csv(source, [item, item])
    with pytest.raises(ValueError, match="Duplicate"):
        analyze(source)


def test_invalid_label_rejected(tmp_path):
    source = tmp_path / "labels.csv"
    write_csv(source, [
        row("a", "one", "v2", category="INVALID"),
        row("a", "two", "v2"),
    ])
    with pytest.raises(ValueError, match="Invalid category"):
        analyze(source)


def test_write_refuses_overwrite_then_backs_up(tmp_path):
    output, matrix, backup = tmp_path / "r.json", tmp_path / "m.csv", tmp_path / "backups"
    output.write_text("old")
    matrix.write_text("old")
    with pytest.raises(FileExistsError):
        write_reports({}, [], output, matrix)
    write_reports({"ok": True}, [], output, matrix, overwrite=True, backup_dir=backup)
    assert json.loads(output.read_text()) == {"ok": True}
    assert len(list(backup.iterdir())) == 2
