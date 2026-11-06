"""Importer provenance: AI drafts must never reach the human annotation file."""
from __future__ import annotations

import csv
from pathlib import Path

import pytest
from openpyxl import Workbook

from scripts import import_labeling_workbook as importer
from scripts.import_labeling_workbook import (
    DRAFT_FIELDS, LEGACY_OUTPUT_FIELDS, OUTPUT_FIELDS, ImportErrorSafe, import_workbooks,
    is_ai_draft_note, read_workbook,
)

HEADERS = ["row_number", "email_id", "sender", "subject", "snippet", "body_preview",
           "is_spam", "needs_response", "priority", "category", "unsure", "note"]


def make_book(path: Path, rows: list[tuple], batch: str = "synthetic_batch", guide: str = "v2.1") -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Labeling"
    ws.append(HEADERS)
    for row in rows:
        ws.append(row)
    progress = wb.create_sheet("Progress")
    progress.append(["Metric", "Value"])
    progress.append(["Batch name", batch])
    progress.append(["Schema version", "v2"])
    progress.append(["Guide version", guide])
    wb.save(path)


def row(n: int, email_id: str, note: str = "", **over) -> tuple:
    values = {"row_number": n, "email_id": email_id, "sender": "person@example.test", "subject": "Synthetic",
              "snippet": "s", "body_preview": "b", "is_spam": "no", "needs_response": "no",
              "priority": "INFORMATIONAL", "category": "JOB", "unsure": "no", "note": note}
    values.update(over)
    return tuple(values[h] for h in HEADERS)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


@pytest.mark.parametrize("note", [
    "claude_draft: labeled by an assistant", "Claude_Draft:x", "  claude_draft:", "CLAUDE-DRAFT :",
    "AI draft: x", "ai_draft:", "ChatGPT-draft : x", "gpt_draft:", "llm draft:", "OpenAI draft:",
])
def test_ai_draft_note_variants_are_detected(note):
    assert is_ai_draft_note(note)


@pytest.mark.parametrize("note", [
    "", None, "synthetic note", "a claude_draft: later in the text", "draft: mine", "claude drafted this",
    "my own draft", "ai", "gpt note: draft",
])
def test_normal_notes_are_not_detected(note):
    assert not is_ai_draft_note(note)


def test_human_import_records_labeler_and_round(tmp_path):
    book = tmp_path / "human.xlsx"
    make_book(book, [row(1, "synthetic-a")])
    records, counts = read_workbook(book)
    assert records[0]["labeler"] == "human" and records[0]["label_round"] == "blind_relabel"
    assert counts["accepted"] == 1 and "draft_note_rows" not in counts
    out = tmp_path / "email_labels_v2.csv"
    totals = import_workbooks([book], out)
    rows = read_csv(out)
    assert [r["labeler"] for r in rows] == ["human"]
    assert tuple(rows[0]) == OUTPUT_FIELDS and totals["quarantined_ai_draft"] == 0
    assert not (tmp_path / "labeling" / "drafts" / "email_labels_ai_drafts.csv").exists()


def test_declared_ai_draft_never_reaches_the_human_file(tmp_path):
    book = tmp_path / "draft.xlsx"
    make_book(book, [row(1, "synthetic-a"), row(2, "synthetic-b")], batch="draft_batch")
    out = tmp_path / "email_labels_v2.csv"
    totals = import_workbooks([book], out, labeler="ai_draft")
    drafts = tmp_path / "labeling" / "drafts" / "email_labels_ai_drafts.csv"
    assert not out.exists(), "no human file should be created from an AI-only import"
    rows = read_csv(drafts)
    assert len(rows) == 2 and tuple(rows[0]) == DRAFT_FIELDS
    assert {r["labeler"] for r in rows} == {"ai_draft"} and {r["label_round"] for r in rows} == {"ai_draft"}
    assert {r["draft_reason"] for r in rows} == {"declared_ai_draft"}
    assert totals["quarantined_ai_draft"] == 2 and totals["quarantined_workbooks"] == 1
    assert totals["accepted"] == 0 and totals["total_records"] == 0


def test_note_marker_quarantines_the_whole_workbook_even_labeled_human(tmp_path):
    """Rows with EMPTY notes in an AI-filled workbook are indistinguishable from human rows,
    so one marker anywhere quarantines the entire workbook."""
    book = tmp_path / "mixed.xlsx"
    make_book(book, [row(1, "synthetic-a", note=""), row(2, "synthetic-b", note="claude_draft: why"), row(3, "synthetic-c")])
    out = tmp_path / "email_labels_v2.csv"
    totals = import_workbooks([book], out, labeler="human")
    assert not out.exists()
    rows = read_csv(tmp_path / "labeling" / "drafts" / "email_labels_ai_drafts.csv")
    assert len(rows) == 3 and {r["draft_reason"] for r in rows} == {"note_prefix_whole_workbook"}
    assert totals["quarantined_ai_draft"] == 3 and totals["draft_note_rows"] == 1


def test_marker_on_an_incomplete_row_still_quarantines_the_workbook(tmp_path):
    book = tmp_path / "w.xlsx"
    make_book(book, [row(1, "synthetic-a"), row(2, "synthetic-b", note="claude_draft: unsure", category=None, unsure="yes")])
    records, counts = read_workbook(book)
    assert counts["draft_note_rows"] == 1 and counts["incomplete"] == 1
    out = tmp_path / "out.csv"
    totals = import_workbooks([book], out)
    assert not out.exists() and totals["quarantined_ai_draft"] == 1


def test_human_and_draft_workbooks_in_one_call_are_split(tmp_path):
    human, draft = tmp_path / "human.xlsx", tmp_path / "draft.xlsx"
    make_book(human, [row(1, "h-1"), row(2, "h-2")], batch="human_batch")
    make_book(draft, [row(1, "d-1", note="claude_draft: x"), row(2, "d-2", note="claude_draft: y")], batch="draft_batch")
    out = tmp_path / "email_labels_v2.csv"
    totals = import_workbooks([human, draft], out)
    assert {r["batch_name"] for r in read_csv(out)} == {"human_batch"}
    assert {r["batch_name"] for r in read_csv(tmp_path / "labeling" / "drafts" / "email_labels_ai_drafts.csv")} == {"draft_batch"}
    assert (totals["accepted"], totals["new_records"], totals["total_records"]) == (2, 2, 2)
    assert (totals["quarantined_ai_draft"], totals["draft_total_records"]) == (2, 2)


def test_every_row_in_the_human_file_has_labeler_human(tmp_path):
    books = []
    for index, batch in enumerate(("b1", "b2", "b3")):
        path = tmp_path / f"{batch}.xlsx"
        make_book(path, [row(1, f"id-{index}"), row(2, f"id2-{index}")], batch=batch)
        books.append(path)
    out = tmp_path / "out.csv"
    import_workbooks(books, out)
    assert {r["labeler"] for r in read_csv(out)} == {"human"}


def test_old_schema_output_is_refused_with_clear_instructions(tmp_path):
    out = tmp_path / "email_labels_v2.csv"
    with out.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=LEGACY_OUTPUT_FIELDS)
        writer.writeheader()
    book = tmp_path / "b.xlsx"
    make_book(book, [row(1, "synthetic-a")])
    before = out.read_bytes()
    with pytest.raises(ImportErrorSafe, match="quarantine_ai_drafts"):
        import_workbooks([book], out, overwrite=True)
    assert out.read_bytes() == before


def test_existing_drafts_file_requires_overwrite_and_nothing_is_written_on_refusal(tmp_path):
    first_draft = tmp_path / "d1.xlsx"
    make_book(first_draft, [row(1, "d-1", note="claude_draft: a")], batch="draft_batch_1")
    out = tmp_path / "out.csv"
    import_workbooks([first_draft], out, labeler="human")  # quarantined by its note
    drafts_path = tmp_path / "labeling" / "drafts" / "email_labels_ai_drafts.csv"
    drafts_before = drafts_path.read_bytes()

    human = tmp_path / "h.xlsx"
    make_book(human, [row(1, "h-1")], batch="human_batch")
    second_draft = tmp_path / "d2.xlsx"
    make_book(second_draft, [row(1, "d-2", note="claude_draft: b")], batch="draft_batch_2")
    with pytest.raises(ImportErrorSafe, match="Drafts file already exists"):
        import_workbooks([human, second_draft], out, labeler="human")
    # The refusal happens before anything is written: no human file, drafts untouched.
    assert not out.exists()
    assert drafts_path.read_bytes() == drafts_before


def test_summary_always_has_the_same_keys(tmp_path):
    human, draft = tmp_path / "h.xlsx", tmp_path / "d.xlsx"
    make_book(human, [row(1, "h-1")], batch="hb")
    make_book(draft, [row(1, "d-1")], batch="db")
    only_human = import_workbooks([human], tmp_path / "a.csv")
    only_draft = import_workbooks([draft], tmp_path / "b.csv", labeler="ai_draft")
    assert set(importer.SUMMARY_KEYS) <= set(only_human) and set(importer.SUMMARY_KEYS) <= set(only_draft)
    assert only_human["quarantined_ai_draft"] == 0 and only_draft["accepted"] == 0


def test_draft_import_is_idempotent_with_overwrite(tmp_path):
    draft = tmp_path / "d.xlsx"
    make_book(draft, [row(1, "d-1"), row(2, "d-2")], batch="draft_batch")
    out = tmp_path / "out.csv"
    import_workbooks([draft], out, labeler="ai_draft")
    second = import_workbooks([draft], out, labeler="ai_draft", overwrite=True)
    assert second["draft_duplicates"] == 2 and second["draft_new_records"] == 0
    drafts_path = tmp_path / "labeling" / "drafts" / "email_labels_ai_drafts.csv"
    assert len(read_csv(drafts_path)) == 2
    assert len(list((drafts_path.parent / "backups").glob("*.csv"))) == 1


def test_custom_drafts_output_path_is_used(tmp_path):
    draft = tmp_path / "d.xlsx"
    make_book(draft, [row(1, "d-1")])
    target = tmp_path / "elsewhere" / "quarantine.csv"
    import_workbooks([draft], tmp_path / "out.csv", labeler="ai_draft", drafts_output=target)
    assert len(read_csv(target)) == 1


def test_unknown_labeler_is_rejected(tmp_path):
    book = tmp_path / "b.xlsx"
    make_book(book, [row(1, "x")])
    with pytest.raises(ImportErrorSafe):
        import_workbooks([book], tmp_path / "o.csv", labeler="robot")
    with pytest.raises(ImportErrorSafe):
        read_workbook(book, "robot")


def test_cli_requires_an_explicit_labeler(tmp_path, capsys):
    book = tmp_path / "b.xlsx"
    make_book(book, [row(1, "x")])
    with pytest.raises(SystemExit) as exc:
        importer.main([str(book), "--output", str(tmp_path / "o.csv")])
    assert exc.value.code == 2
    assert "--labeler" in capsys.readouterr().err


def test_cli_output_contains_counts_only(tmp_path, capsys):
    book = tmp_path / "b.xlsx"
    make_book(book, [row(1, "SECRET-ID-123", note="claude_draft: SECRET NOTE TEXT")])
    code = importer.main([str(book), "--labeler", "human", "--output", str(tmp_path / "o.csv")])
    captured = capsys.readouterr().out
    assert code == 0 and "quarantined" in captured.lower() and "NOT imported as human" in captured
    assert "SECRET" not in captured and "person@example.test" not in captured


def test_read_workbook_itself_stamps_ai_draft_provenance(tmp_path):
    """read_workbook is public API; it must not label AI output as a human blind relabel."""
    book = tmp_path / "d.xlsx"
    make_book(book, [row(1, "d-1")], batch="draft_batch")
    records, _ = read_workbook(book, "ai_draft")
    assert records[0]["labeler"] == "ai_draft" and records[0]["label_round"] == "ai_draft"
    human_records, _ = read_workbook(book, "human")
    assert human_records[0]["labeler"] == "human" and human_records[0]["label_round"] == "blind_relabel"
