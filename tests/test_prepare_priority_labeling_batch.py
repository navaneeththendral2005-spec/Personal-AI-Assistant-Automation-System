from __future__ import annotations
import csv, json
from pathlib import Path
from openpyxl import load_workbook
from scripts.prepare_priority_labeling_batch import build_candidates, read_rows, write_workbook


def test_priority_queue_excludes_frozen_and_only_requires_priority_blank():
    rows = [
        {"email_id":"frozen","true_priority":"","true_category":"FINANCE","sender":"a@x.test","subject":"urgent deadline","snippet":"please respond"},
        {"email_id":"already-priority","true_priority":"INFORMATIONAL","true_category":"","sender":"b@x.test"},
        {"email_id":"category-labeled","true_priority":"","true_category":"FINANCE","sender":"c@x.test","subject":"invoice payment due","snippet":"deadline"},
        {"email_id":"unlabeled","true_priority":"","true_category":"","sender":"d@y.test","subject":"urgent deadline","snippet":"please respond"},
    ]
    selected, counts = build_candidates(rows, {"frozen"}, urgent=1, needs_reply=1, seed=4, max_per_domain=4)
    assert {r["email_id"] for r in selected} == {"category-labeled", "unlabeled"}
    assert counts == {"URGENT": 1, "NEEDS_REPLY": 1}
    assert all("focus_class" in row for row in selected)


def test_workbook_only_exposes_priority_fields_as_editable(tmp_path: Path):
    output = tmp_path / "priority.xlsx"
    write_workbook([{"email_id":"synthetic-id","sender":"synthetic@example.test","subject":"Synthetic","snippet":"Synthetic","body_preview":"Synthetic"}], output)
    wb = load_workbook(output)
    ws = wb["Priority Labeling"]
    assert [c.value for c in ws[1]] == ["row_number","email_id","sender","subject","snippet","body_preview","priority","unsure","note"]
    assert ws["G2"].protection.locked is False
    assert ws["H2"].protection.locked is False
    assert ws["I2"].protection.locked is False
    assert ws["B2"].protection.locked is True
    assert ws.protection.sheet is True
    assert len(ws.data_validations.dataValidation) == 2


def test_duplicate_eligible_ids_fail_closed():
    rows = [{"email_id":"dup","true_priority":""},{"email_id":"dup","true_priority":""}]
    import pytest
    from scripts.prepare_priority_labeling_batch import BatchError
    with pytest.raises(BatchError):
        build_candidates(rows, set())
