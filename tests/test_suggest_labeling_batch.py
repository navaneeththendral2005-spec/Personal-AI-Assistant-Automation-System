"""Task-specific minority sampling safety tests; synthetic rows only."""
import csv
import json

import pytest

from scripts.suggest_labeling_batch import (
    CATEGORY_CLASSES, PRIORITY_CLASSES, SuggestionError, main, suggest_batch, write_queue,
)


def row(i, sender, subject, body, **truth):
    item = {
        "email_id": f"synthetic-{i:03d}", "sender": sender, "subject": subject,
        "snippet": "", "body_text": body, "labels": "INBOX",
        "true_priority": "", "true_needs_response": "", "true_category": "", "true_is_spam": "",
    }
    item.update(truth)
    return item


@pytest.fixture
def rows():
    data = []
    for i in range(8):
        data.append(row(i, f"Bank <alerts{i}@bank{i}.test>", "Invoice payment statement", "Your account statement and payment receipt are available."))
    for i in range(8, 16):
        data.append(row(i, f"Friend <friend{i}@mail.test>", "Re: dinner?", "Hi, can you confirm and reply?"))
    for i in range(16, 24):
        data.append(row(i, f"Service <notice{i}@service{i}.test>", "Account security update", "Your account security alert and delivery status have changed."))
    # Existing unrelated labels must not disqualify a task-specific candidate.
    data.append(row(24, "Friend <a@mail.test>", "Re: request", "Can you confirm?", true_category="PERSONAL", true_is_spam="no"))
    data.append(row(25, "Service <b@service.test>", "Service update", "Your account status changed.", true_priority="INFORMATIONAL", true_is_spam="no"))
    data.append(row(26, "Spam <c@spam.test>", "urgent payment", "Urgent payment required.", true_is_spam="yes"))
    return data


def test_task_specific_sampling_includes_partially_labeled_rows(rows):
    queue, summary = suggest_batch(rows, task="priority", target_per_class=100, max_per_class=100, max_per_domain=100)
    ids = {item["email_id"] for item in queue}
    assert "synthetic-024" in ids  # category label does not block priority sampling
    assert "synthetic-026" not in ids  # spam is never assigned a priority
    assert set(summary) == {x[0] for x in PRIORITY_CLASSES}
    assert all(item["focus_task"] == "priority" for item in queue)


def test_category_sampling_includes_updates_and_partially_labeled_rows(rows):
    queue, summary = suggest_batch(rows, task="category", target_per_class=100, max_per_class=100, max_per_domain=100)
    assert set(summary) == {x[0] for x in CATEGORY_CLASSES}
    assert set(summary) == {
    "JOB",
    "FINANCE",
    "PROMOTIONS",
    "UPDATES",
    "OTHER",
}
    assert "PERSONAL" not in summary  # v2.1 merges legacy PERSONAL into OTHER
    legacy_personal = [item for item in queue if item["email_id"] == "synthetic-024"]
    assert legacy_personal and legacy_personal[0]["focus_class"] == "OTHER"
    assert all(item["focus_task"] == "category" for item in queue)


def test_frozen_ids_are_never_suggested(rows):
    frozen = {"synthetic-000", "synthetic-008", "synthetic-016"}
    queue, _ = suggest_batch(rows, task="priority", frozen_ids=frozen, target_per_class=20, max_per_class=20)
    assert not (frozen & {item["email_id"] for item in queue})


def test_deterministic_queue_contains_no_message_content(rows, tmp_path):
    one, _ = suggest_batch(rows, task="category", target_per_class=5)
    two, _ = suggest_batch(rows, task="category", target_per_class=5)
    assert one == two
    path = tmp_path / "queue.csv"
    write_queue(one, path)
    content = path.read_text(encoding="utf-8-sig")
    assert "subject" not in content.lower()
    assert "statement" not in content.lower()
    assert "body_text" not in content
    with path.open(encoding="utf-8-sig", newline="") as handle:
        assert set(csv.DictReader(handle).fieldnames) == {
            "email_id", "review_reason", "focus_task", "focus_class", "candidate_score"
        }


def test_write_queue_refuses_overwrite(rows, tmp_path):
    queue, _ = suggest_batch(rows, task="priority", target_per_class=3)
    path = tmp_path / "queue.csv"
    write_queue(queue, path)
    before = path.read_bytes()
    with pytest.raises(SuggestionError):
        write_queue(queue, path)
    assert path.read_bytes() == before
    backup = write_queue(queue[:1], path, overwrite=True)
    assert backup is not None and backup.is_file()
    assert backup.read_bytes() == before


def test_cli_requires_valid_existing_frozen_manifest(rows, tmp_path, capsys):
    source = tmp_path / "source.csv"
    with source.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    output = tmp_path / "queue.csv"
    assert main(["--input", str(source), "--output", str(output), "--split-manifest", str(tmp_path / "missing.json")]) == 2
    assert not output.exists()
    manifest = tmp_path / "split.json"
    manifest.write_text(json.dumps({"version": 1, "tasks": {"priority": {"test_ids": ["synthetic-000"]}}}), encoding="utf-8")
    assert main(["--input", str(source), "--output", str(output), "--split-manifest", str(manifest), "--task", "priority"]) == 0
    assert "frozen IDs excluded: 1" in capsys.readouterr().out
