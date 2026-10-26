from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterator

from scripts.label_emails import _fully_labeled, label_dataset, load_rows


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def test_load_rows_adds_spam_field_and_maps_legacy_human_priority(tmp_path: Path) -> None:
    path = tmp_path / "labels.csv"
    write_csv(path, [
        {"email_id": "old-spam", "true_priority": "SPAM"},
        {"email_id": "old-urgent", "true_priority": "URGENT"},
        {"email_id": "unknown", "true_priority": ""},
        {"email_id": "existing", "true_priority": "URGENT", "true_is_spam": "yes"},
    ])
    fields, rows = load_rows(path)
    by_id = {row["email_id"]: row for row in rows}
    assert "true_is_spam" in fields
    assert by_id["old-spam"]["true_is_spam"] == "yes"
    assert by_id["old-spam"]["true_priority"] == "SPAM"
    assert by_id["old-urgent"]["true_is_spam"] == "no"
    assert by_id["unknown"]["true_is_spam"] == ""
    assert by_id["existing"]["true_is_spam"] == "yes"


def test_fully_labeled_uses_split_schema_and_accepts_legacy_spam() -> None:
    base = {"true_needs_response": "no", "true_category": "OTHER"}
    assert _fully_labeled({**base, "true_is_spam": "yes", "true_priority": ""})
    assert _fully_labeled({**base, "true_is_spam": "yes", "true_priority": "SPAM"})
    assert _fully_labeled({**base, "true_is_spam": "no", "true_priority": "URGENT"})
    assert not _fully_labeled({**base, "true_is_spam": "no", "true_priority": "SPAM"})
    assert not _fully_labeled({**base, "true_is_spam": "", "true_priority": "URGENT"})


def test_labeler_spam_skips_priority_and_saves(tmp_path: Path) -> None:
    path = tmp_path / "labels.csv"
    write_csv(path, [{
        "email_id": "synthetic-001", "sender": "offers@example.invalid",
        "subject": "Synthetic offer", "body_text": "Synthetic body",
        "true_is_spam": "", "true_priority": "",
        "true_needs_response": "", "true_category": "",
    }])
    answers: Iterator[str] = iter(["yes", "no", "PROMOTIONS"])
    completed, total = label_dataset(path, input_fn=lambda _p: next(answers))
    saved = read_csv(path)[0]
    assert (completed, total) == (1, 1)
    assert saved["true_is_spam"] == "yes"
    assert saved["true_priority"] == ""
    assert saved["true_needs_response"] == "no"
    assert saved["true_category"] == "PROMOTIONS"


def test_labeler_nonspam_asks_for_priority(tmp_path: Path) -> None:
    path = tmp_path / "labels.csv"
    write_csv(path, [{
        "email_id": "synthetic-002", "true_is_spam": "", "true_priority": "",
        "true_needs_response": "", "true_category": "",
    }])
    answers: Iterator[str] = iter(["no", "URGENT", "yes", "JOB"])
    completed, total = label_dataset(path, input_fn=lambda _p: next(answers))
    saved = read_csv(path)[0]
    assert (completed, total) == (1, 1)
    assert saved["true_is_spam"] == "no"
    assert saved["true_priority"] == "URGENT"
    assert saved["true_needs_response"] == "yes"
    assert saved["true_category"] == "JOB"


def test_quit_saves_progress_without_touching_other_rows(tmp_path: Path) -> None:
    path = tmp_path / "labels.csv"
    write_csv(path, [
        {"email_id": "synthetic-003", "true_is_spam": "", "true_priority": "",
         "true_needs_response": "", "true_category": ""},
        {"email_id": "synthetic-004", "true_is_spam": "no", "true_priority": "INFORMATIONAL",
         "true_needs_response": "no", "true_category": "UPDATES"},
    ])
    answers: Iterator[str] = iter(["no", "q"])
    completed, total = label_dataset(path, input_fn=lambda _p: next(answers))
    saved = {row["email_id"]: row for row in read_csv(path)}
    assert completed == 0
    assert total == 1
    assert saved["synthetic-003"]["true_is_spam"] == "no"
    assert saved["synthetic-004"]["true_priority"] == "INFORMATIONAL"


def test_predictions_are_never_copied_to_true_fields(tmp_path: Path) -> None:
    path = tmp_path / "labels.csv"
    write_csv(path, [{
        "email_id": "synthetic-005", "predicted_is_spam": "yes",
        "predicted_priority": "URGENT", "predicted_needs_response": "no",
        "predicted_category": "OTHER", "true_is_spam": "", "true_priority": "",
        "true_needs_response": "", "true_category": "",
    }])
    answers: Iterator[str] = iter(["no", "INFORMATIONAL", "no", "UPDATES"])
    label_dataset(path, input_fn=lambda _p: next(answers))
    saved = read_csv(path)[0]
    assert saved["true_is_spam"] == "no"
    assert saved["true_priority"] == "INFORMATIONAL"
    assert saved["true_needs_response"] == "no"
    assert saved["true_category"] == "UPDATES"
