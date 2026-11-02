from __future__ import annotations

import csv
from pathlib import Path

import pytest
from openpyxl import Workbook

from scripts.import_labeling_workbook import (
    OUTPUT_FIELDS, ImportErrorSafe, import_workbooks, read_workbook,
)


def make_book(
    path: Path,
    rows: list[tuple],
    batch: str = "synthetic_batch",
    guide: str = "v2.1",
    schema: str = "v2",
) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Labeling"
    ws.append(["row_number", "email_id", "sender", "subject", "snippet", "body_preview", "is_spam", "needs_response", "priority", "category", "unsure", "note"])
    for row in rows:
        ws.append(row)
    progress = wb.create_sheet("Progress")
    progress.append(["Metric", "Value"])
    progress.append(["Batch name", batch])
    progress.append(["Mode", "relabel"])
    progress.append(["Purpose", "synthetic test"])
    progress.append(["Schema version", schema])
    progress.append(["Guide version", guide])
    wb.save(path)


def valid_row(n: int, email_id: str = "synthetic-1", **overrides) -> tuple:
    values = {
        "row_number": n, "email_id": email_id, "sender": "person@example.test",
        "subject": "Synthetic subject", "snippet": "Synthetic snippet", "body_preview": "Synthetic body",
        "is_spam": "no", "needs_response": "no", "priority": "INFORMATIONAL",
        "category": "PERSONAL", "unsure": "no", "note": "synthetic note",
    }
    values.update(overrides)
    return tuple(values[key] for key in ("row_number", "email_id", "sender", "subject", "snippet", "body_preview", "is_spam", "needs_response", "priority", "category", "unsure", "note"))


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def test_import_valid_row_and_preserve_provenance_without_email_context(tmp_path: Path) -> None:
    book = tmp_path / "synthetic.xlsx"
    make_book(book, [valid_row(1)])
    records, counts = read_workbook(book)
    assert len(records) == 1
    assert counts["accepted"] == 1
    assert records[0]["guide_version"] == "v2.1"
    assert records[0]["batch_name"] == "synthetic_batch"
    assert set(records[0]) == set(OUTPUT_FIELDS)
    assert "sender" not in records[0] and "subject" not in records[0]


def test_incomplete_invalid_and_consistency_conflict_are_excluded(tmp_path: Path) -> None:
    book = tmp_path / "checks.xlsx"
    make_book(book, [
        valid_row(1, "synthetic-incomplete", priority=""),
        valid_row(2, "synthetic-invalid", category="UNKNOWN"),
        valid_row(3, "synthetic-conflict", needs_response="yes", priority="INFORMATIONAL"),
        valid_row(4, "synthetic-valid"),
    ])
    records, counts = read_workbook(book)
    assert [r["email_id"] for r in records] == ["synthetic-valid"]
    assert counts["incomplete"] == 1
    assert counts["invalid"] == 1
    assert counts["conflict"] == 1


def test_duplicate_same_email_inside_one_workbook_is_deduplicated_or_flagged(tmp_path: Path) -> None:
    book = tmp_path / "duplicates.xlsx"
    make_book(book, [
        valid_row(1, "synthetic-same"), valid_row(2, "synthetic-same"),
        valid_row(3, "synthetic-conflict"), valid_row(4, "synthetic-conflict", category="FINANCE"),
    ])
    records, counts = read_workbook(book)
    assert [r["email_id"] for r in records] == ["synthetic-same"]
    assert counts["duplicates"] == 1
    assert counts["conflict"] == 1


def test_import_is_idempotent_for_same_annotation_identity(tmp_path: Path) -> None:
    book = tmp_path / "batch.xlsx"
    make_book(book, [valid_row(1, "synthetic-a"), valid_row(2, "synthetic-b")])
    output = tmp_path / "email_labels_v2.csv"
    first = import_workbooks([book], output)
    assert first["new_records"] == 2
    assert len(read_csv(output)) == 2
    with pytest.raises(ImportErrorSafe, match="already exists"):
        import_workbooks([book], output)
    second = import_workbooks([book], output, overwrite=True)
    assert second["duplicates"] == 2
    assert second["new_records"] == 0
    assert len(read_csv(output)) == 2
    assert len(list((tmp_path / "backups").glob("*.csv"))) == 1


def test_same_email_across_guide_versions_is_preserved_as_two_annotations(tmp_path: Path) -> None:
    old, new = tmp_path / "old.xlsx", tmp_path / "new.xlsx"
    make_book(old, [valid_row(1, "synthetic-x", category="UPDATES")], batch="old_batch", guide="v2.0-draft")
    make_book(new, [valid_row(1, "synthetic-x", category="PROMOTIONS")], batch="new_batch", guide="v2.1")
    output = tmp_path / "out.csv"
    result = import_workbooks([old, new], output)
    rows = read_csv(output)
    assert result["new_records"] == 2
    assert result.get("conflict", 0) == 0
    assert len(rows) == 2
    assert {r["guide_version"] for r in rows} == {"v2.0-draft", "v2.1"}
    assert {r["category"] for r in rows} == {"UPDATES", "PROMOTIONS"}


def test_same_email_across_distinct_batches_same_guide_is_preserved(tmp_path: Path) -> None:
    one, two = tmp_path / "one.xlsx", tmp_path / "two.xlsx"
    make_book(one, [valid_row(1, "synthetic-x")], batch="batch_one")
    make_book(two, [valid_row(1, "synthetic-x", category="FINANCE")], batch="batch_two")
    output = tmp_path / "out.csv"
    result = import_workbooks([one, two], output)
    assert result["new_records"] == 2
    assert result.get("conflict", 0) == 0
    assert len(read_csv(output)) == 2


def test_same_annotation_identity_with_different_labels_is_conflict(tmp_path: Path) -> None:
    one, two = tmp_path / "one.xlsx", tmp_path / "two.xlsx"
    make_book(one, [valid_row(1, "synthetic-x")], batch="same_batch")
    make_book(two, [valid_row(1, "synthetic-x", category="FINANCE")], batch="same_batch")
    output = tmp_path / "out.csv"
    result = import_workbooks([one, two], output)
    assert result["conflict"] == 1
    assert result["new_records"] == 0
    assert read_csv(output) == []


def test_existing_email_can_receive_new_annotation_without_replacing_old(tmp_path: Path) -> None:
    old, new = tmp_path / "old.xlsx", tmp_path / "new.xlsx"
    make_book(old, [valid_row(1, "synthetic-x", category="UPDATES")], batch="old_batch", guide="v2.0-draft")
    make_book(new, [valid_row(1, "synthetic-x", category="PROMOTIONS")], batch="new_batch", guide="v2.1")
    output = tmp_path / "out.csv"
    import_workbooks([old], output)
    result = import_workbooks([new], output, overwrite=True)
    rows = read_csv(output)
    assert result["new_records"] == 1
    assert result["existing_records"] == 1
    assert len(rows) == 2
    assert {r["guide_version"] for r in rows} == {"v2.0-draft", "v2.1"}


def test_wrong_workbook_schema_is_rejected(tmp_path: Path) -> None:
    book = tmp_path / "wrong.xlsx"
    wb = Workbook()
    wb.active.title = "Labeling"
    wb.active.append(["email_id"])
    progress = wb.create_sheet("Progress")
    progress.append(["Metric", "Value"])
    progress.append(["Batch name", "b"])
    progress.append(["Schema version", "v2"])
    progress.append(["Guide version", "v2.1"])
    wb.save(book)
    with pytest.raises(ImportErrorSafe, match="columns"):
        read_workbook(book)
