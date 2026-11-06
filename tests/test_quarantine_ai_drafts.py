"""Retroactive cleanup: every batch needs an explicit human/AI decision."""
from __future__ import annotations

import csv
from pathlib import Path

import pytest
from openpyxl import Workbook

from scripts import quarantine_ai_drafts as quarantine_module
from scripts.import_labeling_workbook import (
    DRAFT_FIELDS, LEGACY_OUTPUT_FIELDS, OUTPUT_FIELDS, import_workbooks,
)
from scripts.quarantine_ai_drafts import QuarantineError, classify_rows, describe_batches, quarantine

SECRET_ID, SECRET_NOTE = "SECRET-EMAIL-ID-77", "SECRET NOTE TEXT"


def legacy_row(email_id: str, batch: str, guide: str = "v2.1", note: str = "", **over) -> dict[str, str]:
    base = {"email_id": email_id, "is_spam": "no", "needs_response": "no", "priority": "INFORMATIONAL",
            "category": "JOB", "unsure": "no", "note": note, "schema_version": "v2", "guide_version": guide,
            "label_round": "blind_relabel", "batch_name": batch, "imported_at": "2026-11-01T10:00:00+00:00"}
    base.update(over)
    return base


def write_csv(path: Path, rows: list[dict[str, str]], fields=LEGACY_OUTPUT_FIELDS) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


@pytest.fixture()
def labels(tmp_path):
    """The real-world situation: three batches already imported as 'human'."""
    rows = (
        [legacy_row(f"pilot-{i}", "pilot", "v2.1", note="claude_draft: x" if i == 0 else "") for i in range(3)]
        + [legacy_row(f"mid-{i}", "batch_21_60", "v2.0-draft") for i in range(4)]
        + [legacy_row(f"late-{i}", "batch_61_81", "v2.0-draft") for i in range(2)]
        + [legacy_row(f"mine-{i}", "my_own_batch", "v2.1") for i in range(5)]
    )
    path = tmp_path / "email_labels_v2.csv"
    write_csv(path, rows)
    return path


def test_describe_batches_is_aggregate_only(labels):
    rows = read_csv(labels)
    overview = {(e["batch_name"], e["guide_version"]): e for e in describe_batches(rows)}
    assert overview[("pilot", "v2.1")]["rows"] == 3 and overview[("pilot", "v2.1")]["rows_with_ai_draft_note"] == 1
    assert overview[("batch_21_60", "v2.0-draft")]["labelers"] == {"(not recorded)": 4}
    assert set(overview) == {("pilot", "v2.1"), ("batch_21_60", "v2.0-draft"), ("batch_61_81", "v2.0-draft"), ("my_own_batch", "v2.1")}


def test_undecided_batches_are_refused_and_nothing_is_written(labels, tmp_path):
    drafts = tmp_path / "labeling" / "drafts" / "d.csv"
    with pytest.raises(QuarantineError, match="not declared"):
        quarantine(labels, draft_batches=["batch_21_60"], drafts_output=drafts)
    assert not drafts.exists() and not (tmp_path / "email_labels_v2_human.csv").exists()


def test_declared_split_writes_clean_human_and_draft_files(labels, tmp_path):
    drafts = tmp_path / "labeling" / "drafts" / "d.csv"
    before = labels.read_bytes()
    summary = quarantine(
        labels, draft_batches=["batch_21_60", "batch_61_81"], confirmed_human_batches=["my_own_batch"],
        drafts_output=drafts,
    )
    assert labels.read_bytes() == before and summary["input_modified"] is False
    # "pilot" is quarantined by its note even though it was never declared.
    assert summary["draft_batches"] == ["batch_21_60", "batch_61_81", "pilot"]
    assert summary["human_batches"] == ["my_own_batch"]
    assert (summary["input_rows"], summary["human_rows"], summary["ai_draft_rows"]) == (14, 5, 9)
    assert summary["ai_draft_reasons"] == {"declared_batch": 6, "note_prefix": 3}

    human = read_csv(tmp_path / "email_labels_v2_human.csv")
    assert len(human) == 5 and {r["labeler"] for r in human} == {"human"}
    assert tuple(human[0]) == OUTPUT_FIELDS
    draft_rows = read_csv(drafts)
    assert len(draft_rows) == 9 and tuple(draft_rows[0]) == DRAFT_FIELDS
    assert {r["labeler"] for r in draft_rows} == {"ai_draft"} and {r["label_round"] for r in draft_rows} == {"ai_draft"}
    assert not ({r["batch_name"] for r in human} & {r["batch_name"] for r in draft_rows})


def test_note_marker_quarantines_the_whole_batch_including_empty_notes(labels, tmp_path):
    summary = quarantine(
        labels, draft_batches=["batch_21_60", "batch_61_81"], confirmed_human_batches=["my_own_batch"],
        drafts_output=tmp_path / "d.csv",
    )
    pilot_rows = [r for r in read_csv(tmp_path / "d.csv") if r["batch_name"] == "pilot"]
    assert len(pilot_rows) == 3 and {r["note"] == "" for r in pilot_rows} == {True, False}
    assert summary["ai_draft_reasons"]["note_prefix"] == 3


def test_draft_guide_version_declares_every_batch_under_it(labels, tmp_path):
    summary = quarantine(
        labels, draft_guide_versions=["v2.0-draft"], confirmed_human_batches=["my_own_batch"],
        drafts_output=tmp_path / "d.csv",
    )
    assert summary["ai_draft_reasons"]["declared_guide_version"] == 6
    assert summary["human_rows"] == 5


def test_a_batch_cannot_be_both_draft_and_human(labels, tmp_path):
    with pytest.raises(QuarantineError, match="both"):
        quarantine(labels, draft_batches=["pilot"], confirmed_human_batches=["pilot"], drafts_output=tmp_path / "d.csv")


def test_in_place_backs_up_and_replaces_the_input(labels, tmp_path):
    original = labels.read_bytes()
    summary = quarantine(
        labels, draft_batches=["batch_21_60", "batch_61_81", "pilot"], confirmed_human_batches=["my_own_batch"],
        drafts_output=tmp_path / "d.csv", in_place=True,
    )
    assert summary["input_modified"] is True
    assert len(read_csv(labels)) == 5 and {r["labeler"] for r in read_csv(labels)} == {"human"}
    backups = list((tmp_path / "backups").glob("*.csv"))
    assert len(backups) == 1 and backups[0].read_bytes() == original


def test_outputs_are_never_overwritten_without_the_flag(labels, tmp_path):
    kwargs = dict(draft_batches=["batch_21_60", "batch_61_81"], confirmed_human_batches=["my_own_batch"], drafts_output=tmp_path / "d.csv")
    quarantine(labels, **kwargs)
    human_before, drafts_before = (tmp_path / "email_labels_v2_human.csv").read_bytes(), (tmp_path / "d.csv").read_bytes()
    with pytest.raises(QuarantineError, match="already exists"):
        quarantine(labels, **kwargs)
    assert (tmp_path / "email_labels_v2_human.csv").read_bytes() == human_before
    assert (tmp_path / "d.csv").read_bytes() == drafts_before
    again = quarantine(labels, overwrite=True, **kwargs)
    assert again["ai_draft_rows_new_in_drafts_file"] == 0  # idempotent: no duplicate draft rows
    assert len(read_csv(tmp_path / "d.csv")) == 9


def test_rows_already_marked_ai_draft_in_a_new_schema_file_stay_quarantined(tmp_path):
    rows = [legacy_row("a", "b1", labeler="human"), legacy_row("b", "b2", labeler="ai_draft")]
    path = tmp_path / "labels.csv"
    write_csv(path, rows, OUTPUT_FIELDS)
    human, drafts = classify_rows(read_csv(path), confirmed_human_batches=["b1"])
    assert [r["email_id"] for r in human] == ["a"] and [r["email_id"] for r in drafts] == ["b"]
    assert drafts[0]["draft_reason"] == "previously_marked"


def test_unsupported_schema_and_missing_input_are_rejected(tmp_path):
    with pytest.raises(QuarantineError):
        quarantine(tmp_path / "missing.csv")
    bad = tmp_path / "bad.csv"
    bad.write_text("a,b\n1,2\n", encoding="utf-8")
    with pytest.raises(QuarantineError, match="unsupported schema"):
        quarantine(bad)
    empty = tmp_path / "empty.csv"
    write_csv(empty, [])
    with pytest.raises(QuarantineError, match="no annotation rows"):
        quarantine(empty)


def test_cleaned_file_accepts_new_human_imports_but_the_old_file_does_not(labels, tmp_path):
    book = tmp_path / "new_human.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "Labeling"
    ws.append(["row_number", "email_id", "sender", "subject", "snippet", "body_preview", "is_spam", "needs_response",
               "priority", "category", "unsure", "note"])
    ws.append([1, "fresh-1", "p@example.test", "s", "s", "b", "no", "no", "INFORMATIONAL", "JOB", "no", ""])
    progress = wb.create_sheet("Progress")
    for line in (["Metric", "Value"], ["Batch name", "fresh_pilot"], ["Schema version", "v2"], ["Guide version", "v2.1"]):
        progress.append(line)
    wb.save(book)

    with pytest.raises(Exception, match="quarantine_ai_drafts"):
        import_workbooks([book], labels, overwrite=True)  # old schema is refused
    quarantine(labels, draft_batches=["batch_21_60", "batch_61_81"], confirmed_human_batches=["my_own_batch"],
               drafts_output=tmp_path / "labeling" / "drafts" / "email_labels_ai_drafts.csv", in_place=True)
    totals = import_workbooks([book], labels, overwrite=True)
    assert totals["new_records"] == 1 and totals["total_records"] == 6
    assert {r["labeler"] for r in read_csv(labels)} == {"human"}


def test_cli_list_and_split_print_counts_and_batch_names_only(labels, tmp_path, capsys):
    rows = read_csv(labels)
    rows[0]["note"] = f"claude_draft: {SECRET_NOTE}"
    rows[1]["email_id"] = SECRET_ID
    write_csv(labels, rows)
    assert quarantine_module.main(["--input", str(labels), "--list"]) == 0
    listing = capsys.readouterr().out
    assert "batch=pilot" in listing and "rows_with_ai_draft_note=1" in listing
    assert SECRET_ID not in listing and SECRET_NOTE not in listing

    code = quarantine_module.main([
        "--input", str(labels), "--draft-batch", "batch_21_60", "--draft-batch", "batch_61_81",
        "--confirm-human-batch", "my_own_batch", "--drafts-output", str(tmp_path / "d.csv"),
    ])
    out = capsys.readouterr().out
    assert code == 0 and "human_rows: 5" in out and SECRET_ID not in out and SECRET_NOTE not in out
    assert quarantine_module.main(["--input", str(labels), "--draft-batch", "pilot"]) == 2
    assert "not declared" in capsys.readouterr().err


def test_warns_when_no_human_annotations_remain(tmp_path, capsys):
    rows = [legacy_row(f"a-{i}", "ai_only", note="claude_draft: x") for i in range(3)]
    path = tmp_path / "labels.csv"
    write_csv(path, rows)
    summary = quarantine(path, drafts_output=tmp_path / "d.csv")
    assert summary["human_rows"] == 0 and "No human annotations remain" in summary["warnings"][0]
    assert quarantine_module.main(["--input", str(path), "--human-output", str(tmp_path / "h2.csv"),
                                   "--drafts-output", str(tmp_path / "d2.csv")]) == 0
    assert "WARNING: No human annotations remain" in capsys.readouterr().out
    clean = quarantine(path, drafts_output=tmp_path / "d3.csv", human_output=tmp_path / "h3.csv")
    assert clean["warnings"] != []


def test_no_warning_when_human_rows_exist(labels, tmp_path):
    summary = quarantine(labels, draft_batches=["batch_21_60", "batch_61_81"], confirmed_human_batches=["my_own_batch"],
                         drafts_output=tmp_path / "d.csv")
    assert summary["warnings"] == []
