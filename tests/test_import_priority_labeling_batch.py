from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
from openpyxl import Workbook

from scripts.import_priority_labeling_batch import (
    PriorityImportError, prepare_priority_import,
)

HEADERS = ["row_number", "email_id", "sender", "subject", "snippet", "body_preview", "priority", "unsure", "note"]


def make_workbook(path: Path, rows: list[tuple]) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Priority Labeling"
    ws.append(HEADERS)
    for row in rows:
        ws.append(row)
    guide = wb.create_sheet("Guide")
    guide.append(["# FRIDAY Email Labeling Guide — v2.1"])
    guide.append(["**Status:** FROZEN — use this version for blind human ground-truth labeling"])
    guide.append(["**Schema version:** `v2.1`"])
    guide.append(["**Guide version:** `v2.1`"])
    progress = wb.create_sheet("Progress")
    progress.append(["Metric", "Value"])
    progress.append(["Rows", len(rows)])
    progress.append(["Completed", len(rows)])
    wb.save(path)


def make_dataset(path: Path, rows: list[dict[str, str]]) -> None:
    fields = ["email_id", "sender", "subject", "snippet", "body_text", "true_priority", "predicted_priority"]
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def make_manifest(path: Path, frozen: list[str] | None = None) -> None:
    path.write_text(json.dumps({"version": 1, "tasks": {"priority": {"test_ids": frozen or []}}}), encoding="utf-8")


def row(n: int, email_id: str, priority: str = "INFORMATIONAL", unsure: str = "no") -> tuple:
    return (n, email_id, "synthetic@example.test", "Synthetic", "Synthetic", "Synthetic", priority, unsure, "synthetic note")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def test_imports_only_certain_rows_and_preserves_every_other_column(tmp_path: Path):
    book, source, manifest, output = (tmp_path / name for name in ("batch.xlsx", "source.csv", "split.json", "output.csv"))
    make_workbook(book, [row(1, "id-a", "INFORMATIONAL", "no"), row(2, "id-b", "NEEDS_REPLY", "yes")])
    make_dataset(source, [
        {"email_id": "id-a", "sender": "a", "subject": "s", "snippet": "n", "body_text": "b", "true_priority": "", "predicted_priority": "URGENT"},
        {"email_id": "id-b", "sender": "b", "subject": "s", "snippet": "n", "body_text": "b", "true_priority": "", "predicted_priority": "NEEDS_REPLY"},
    ])
    make_manifest(manifest)
    before_source = source.read_bytes()
    before_book = book.read_bytes()
    before_manifest = manifest.read_bytes()

    counts = prepare_priority_import(book, source, manifest, output)
    rows = read_csv(output)
    assert rows[0]["true_priority"] == "INFORMATIONAL"
    assert rows[1]["true_priority"] == ""
    assert rows[0]["predicted_priority"] == "URGENT"
    assert counts["verified_labels"] == 1
    assert counts["uncertain_excluded"] == 1
    assert counts["new_labels_applied"] == 1
    assert source.read_bytes() == before_source
    assert book.read_bytes() == before_book
    assert manifest.read_bytes() == before_manifest


def test_refuses_any_workbook_id_in_frozen_priority_holdout(tmp_path: Path):
    book, source, manifest, output = (tmp_path / name for name in ("batch.xlsx", "source.csv", "split.json", "output.csv"))
    make_workbook(book, [row(1, "frozen")])
    make_dataset(source, [{"email_id": "frozen", "true_priority": "", "predicted_priority": ""}])
    make_manifest(manifest, ["frozen"])
    with pytest.raises(PriorityImportError, match="frozen"):
        prepare_priority_import(book, source, manifest, output)
    assert not output.exists()


def test_refuses_missing_or_malformed_manifest(tmp_path: Path):
    book, source, manifest, output = (tmp_path / name for name in ("batch.xlsx", "source.csv", "split.json", "output.csv"))
    make_workbook(book, [row(1, "id-a")])
    make_dataset(source, [{"email_id": "id-a", "true_priority": "", "predicted_priority": ""}])
    with pytest.raises(PriorityImportError, match="manifest is missing"):
        prepare_priority_import(book, source, manifest, output)
    make_manifest(manifest)
    manifest.write_text('{bad', encoding="utf-8")
    with pytest.raises(PriorityImportError, match="unreadable"):
        prepare_priority_import(book, source, manifest, output)
    assert not output.exists()


def test_refuses_workbook_id_not_in_source(tmp_path: Path):
    book, source, manifest, output = (tmp_path / name for name in ("batch.xlsx", "source.csv", "split.json", "output.csv"))
    make_workbook(book, [row(1, "missing")])
    make_dataset(source, [{"email_id": "other", "true_priority": "", "predicted_priority": ""}])
    make_manifest(manifest)
    with pytest.raises(PriorityImportError, match="do not match"):
        prepare_priority_import(book, source, manifest, output)
    assert not output.exists()


def test_refuses_conflict_with_existing_ground_truth(tmp_path: Path):
    book, source, manifest, output = (tmp_path / name for name in ("batch.xlsx", "source.csv", "split.json", "output.csv"))
    make_workbook(book, [row(1, "id-a", "URGENT")])
    make_dataset(source, [{"email_id": "id-a", "true_priority": "INFORMATIONAL", "predicted_priority": ""}])
    make_manifest(manifest)
    with pytest.raises(PriorityImportError, match="conflicts"):
        prepare_priority_import(book, source, manifest, output)
    assert not output.exists()


def test_matching_existing_truth_is_idempotent(tmp_path: Path):
    book, source, manifest, output = (tmp_path / name for name in ("batch.xlsx", "source.csv", "split.json", "output.csv"))
    make_workbook(book, [row(1, "id-a", "INFORMATIONAL")])
    make_dataset(source, [{"email_id": "id-a", "true_priority": "INFORMATIONAL", "predicted_priority": ""}])
    make_manifest(manifest)
    counts = prepare_priority_import(book, source, manifest, output)
    assert counts["already_matching_labels"] == 1
    assert counts["new_labels_applied"] == 0


def test_refuses_to_overwrite_existing_output(tmp_path: Path):
    book, source, manifest, output = (tmp_path / name for name in ("batch.xlsx", "source.csv", "split.json", "output.csv"))
    make_workbook(book, [row(1, "id-a")])
    make_dataset(source, [{"email_id": "id-a", "true_priority": "", "predicted_priority": ""}])
    make_manifest(manifest)
    output.write_text("keep me", encoding="utf-8")
    with pytest.raises(PriorityImportError, match="never overwritten"):
        prepare_priority_import(book, source, manifest, output)
    assert output.read_text(encoding="utf-8") == "keep me"


def test_duplicate_dataset_ids_fail_closed(tmp_path: Path):
    book, source, manifest, output = (tmp_path / name for name in ("batch.xlsx", "source.csv", "split.json", "output.csv"))
    make_workbook(book, [row(1, "id-a")])
    make_dataset(source, [
        {"email_id": "id-a", "true_priority": "", "predicted_priority": ""},
        {"email_id": "id-a", "true_priority": "", "predicted_priority": ""},
    ])
    make_manifest(manifest)
    with pytest.raises(PriorityImportError, match="duplicate IDs"):
        prepare_priority_import(book, source, manifest, output)
    assert not output.exists()


def test_duplicate_workbook_ids_fail_closed(tmp_path: Path):
    book, source, manifest, output = (tmp_path / name for name in ("batch.xlsx", "source.csv", "split.json", "output.csv"))
    make_workbook(book, [row(1, "id-a"), row(2, "id-a")])
    make_dataset(source, [{"email_id": "id-a", "true_priority": "", "predicted_priority": ""}])
    make_manifest(manifest)
    with pytest.raises(PriorityImportError, match="duplicate IDs"):
        prepare_priority_import(book, source, manifest, output)
    assert not output.exists()


def test_requires_priority_workbook_schema(tmp_path: Path):
    book, source, manifest, output = (tmp_path / name for name in ("batch.xlsx", "source.csv", "split.json", "output.csv"))
    wb = Workbook()
    wb.active.title = "Labeling"
    wb.save(book)
    make_dataset(source, [{"email_id": "id-a", "true_priority": "", "predicted_priority": ""}])
    make_manifest(manifest)
    with pytest.raises(PriorityImportError, match="Priority Labeling"):
        prepare_priority_import(book, source, manifest, output)
    assert not output.exists()
