"""Labeling-queue suggestions: heuristic candidates only, never labels."""
import csv

import pytest

from scripts.label_emails import _load_queue_ids
from scripts.suggest_labeling_batch import FOCUS_CLASSES, SuggestionError, main, suggest_batch, write_queue


def _row(i, sender, subject, body, **truth):
    row = {"email_id": f"id{i:03d}", "sender": sender, "subject": subject, "snippet": "", "body_text": body, "labels": "INBOX",
           "true_priority": "", "true_needs_response": "", "true_category": "", "true_is_spam": ""}
    row.update(truth)
    return row


@pytest.fixture()
def rows():
    data = []
    for i in range(6):
        data.append(_row(i, f"Bank <alerts@bank{i}.com>", f"Invoice payment due {i}", "Your invoice payment of Rs. 500 is due. Statement attached."))
    for i in range(6, 12):
        data.append(_row(i, f"Friend <friend{i}@gmail.com>", f"Re: dinner {i}?", "Hi Navaneeth, can you confirm? Please let me know."))
    for i in range(12, 18):
        data.append(_row(i, f"Deals <news@promo{i}.com>", f"Sale {i}% off", "Limited time discount. Unsubscribe here."))
    for i in range(18, 22):
        data.append(_row(i, "x@y.com", "labeled already", "text", true_priority="URGENT", true_is_spam="no", true_needs_response="no", true_category="FINANCE"))
    return data


def test_suggestions_only_come_from_unlabeled_rows(rows):
    queue, _ = suggest_batch(rows, target_per_class=5, max_per_class=5)
    labeled = {r["email_id"] for r in rows if r["true_priority"]}
    assert queue and not ({q["email_id"] for q in queue} & labeled)
    assert len({q["email_id"] for q in queue}) == len(queue)  # one focus class per email


def test_candidates_match_their_focus_class(rows):
    queue, _ = suggest_batch(rows, target_per_class=30, max_per_class=3)
    by_focus = {}
    for item in queue:
        by_focus.setdefault(item["focus_class"], []).append(int(item["email_id"][2:]))
    assert by_focus["FINANCE"] and all(i < 6 for i in by_focus["FINANCE"])
    assert by_focus["NEEDS_REPLY"] and all(6 <= i < 12 for i in by_focus["NEEDS_REPLY"])


def test_request_size_respects_target_cap_and_existing_counts(rows):
    _, summary = suggest_batch(rows, target_per_class=6, max_per_class=2)
    assert set(summary) == {focus for focus, _, _ in FOCUS_CLASSES}
    assert summary["NEEDS_REPLY"]["requested"] == 2
    assert summary["FINANCE"]["verified_now"] == 4 and summary["FINANCE"]["requested"] == 2
    assert summary["URGENT"]["verified_now"] == 4
    _, none_needed = suggest_batch(rows, target_per_class=0)
    assert all(info["requested"] == 0 and info["suggested"] == 0 for info in none_needed.values())


def test_domain_diversity_cap(rows):
    same_domain = [_row(100 + i, f"Bank <alerts{i}@samebank.com>", "Invoice payment due", "Invoice payment due, statement") for i in range(8)]
    queue, _ = suggest_batch(same_domain, target_per_class=20, max_per_class=20, max_per_domain=2)
    assert len([q for q in queue if q["focus_class"] == "FINANCE"]) <= 2


def test_selection_is_deterministic_and_ignores_prediction_columns(rows):
    first, _ = suggest_batch(rows, target_per_class=5)
    noisy = [{**r, "predicted_priority": "URGENT", "rules_category": "JOB", "llm_priority": "SPAM"} for r in rows]
    second, _ = suggest_batch(noisy, target_per_class=5)
    assert first == second


def test_queue_contains_no_content_and_works_with_the_labeling_tool(rows, tmp_path):
    queue, _ = suggest_batch(rows, target_per_class=5)
    out = tmp_path / "queue.csv"
    write_queue(queue, out)
    with out.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames == ["email_id", "review_reason", "focus_class", "candidate_score"]
        content = out.read_text(encoding="utf-8")
    for secret in ("Bank", "gmail.com", "Invoice", "Navaneeth"):
        assert secret not in content
    assert _load_queue_ids(out) == {q["email_id"] for q in queue}


def test_output_is_never_overwritten_without_flag(rows, tmp_path):
    queue, _ = suggest_batch(rows, target_per_class=5)
    out = tmp_path / "queue.csv"
    write_queue(queue, out)
    before = out.read_bytes()
    with pytest.raises(SuggestionError):
        write_queue(queue[:1], out)
    assert out.read_bytes() == before
    write_queue(queue[:1], out, overwrite=True)
    assert out.read_bytes() != before


def test_cli(rows, tmp_path, capsys):
    data = tmp_path / "d.csv"
    with data.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    out = tmp_path / "q.csv"
    assert main(["--input", str(data), "--output", str(out), "--target-per-class", "5"]) == 0
    assert "no labels were assigned" in capsys.readouterr().out
    assert main(["--input", str(data), "--output", str(out)]) == 2  # exists
    assert main(["--input", str(tmp_path / "missing.csv"), "--output", str(tmp_path / "x.csv")]) == 2
    bad = tmp_path / "bad.csv"
    bad.write_text("a,b\n1,2\n", encoding="utf-8")
    assert main(["--input", str(bad), "--output", str(tmp_path / "y.csv")]) == 2
