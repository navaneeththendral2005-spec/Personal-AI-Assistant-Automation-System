from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from scripts.check_label_agreement import cohen_kappa, sample_relabel_queue, score_agreement


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def make_split(path: Path, frozen: list[str]) -> None:
    path.write_text(json.dumps({"version": 1, "tasks": {"priority": {"test_ids": frozen}}}), encoding="utf-8")


def test_relabel_sample_is_verified_dev_only_and_blind(tmp_path: Path) -> None:
    source = tmp_path / "labels.csv"
    rows = []
    for i in range(40):
        rows.append({
            "email_id": f"id-{i:03d}", "sender": "synthetic@example.test",
            "subject": f"Synthetic {i}", "snippet": "Synthetic snippet",
            "body_text": "Synthetic body", "labels": "INBOX",
            "true_priority": "URGENT" if i < 35 else "",
            "true_needs_response": "no" if i < 35 else "",
            "true_category": "OTHER" if i < 35 else "",
            "predicted_priority": "SPAM",
        })
    write_csv(source, rows)
    split = tmp_path / "split.json"
    frozen = [f"id-{i:03d}" for i in range(5)]
    make_split(split, frozen)

    manifest = tmp_path / "relabel_manifest.json"
    output = tmp_path / "relabel_review.csv"
    result = sample_relabel_queue(source, split_manifest_path=split, manifest_path=manifest,
                                  output_path=output, sample_size=30, seed=23)
    ids = json.loads(manifest.read_text(encoding="utf-8"))["email_ids"]
    assert len(ids) == len(set(ids)) == 30
    assert not set(ids) & set(frozen)
    review = read_csv(output)
    assert all(row["true_priority"] == "" for row in review)
    assert all(row["true_needs_response"] == "" for row in review)
    assert all(row["true_category"] == "" for row in review)
    assert all("predicted_priority" not in row for row in review)
    assert result["sample_size"] == 30


def test_relabel_resume_preserves_second_annotations(tmp_path: Path) -> None:
    source = tmp_path / "labels.csv"
    write_csv(source, [{"email_id": f"id-{i}", "true_priority": "URGENT"} for i in range(10)])
    split = tmp_path / "split.json"
    make_split(split, [])
    manifest = tmp_path / "manifest.json"
    output = tmp_path / "review.csv"
    sample_relabel_queue(source, split_manifest_path=split, manifest_path=manifest,
                         output_path=output, sample_size=5, seed=5)
    rows = read_csv(output)
    rows[0]["true_priority"] = "INFORMATIONAL"
    write_csv(output, rows)
    before = output.read_bytes()
    manifest_before = manifest.read_bytes()
    sample_relabel_queue(source, split_manifest_path=split, manifest_path=manifest,
                         output_path=output, sample_size=5, seed=999)
    assert output.read_bytes() == before
    assert manifest.read_bytes() == manifest_before


def test_cohen_kappa_perfect_and_disagreement_cases() -> None:
    assert cohen_kappa(["yes", "no", "yes", "no"], ["yes", "no", "yes", "no"]) == 1.0
    assert cohen_kappa([], []) is None
    assert cohen_kappa(["yes", "no"], ["no", "yes"]) == -1.0
    with pytest.raises(ValueError, match="equal length"):
        cohen_kappa(["yes"], [])


def test_score_agreement_uses_only_valid_pairs_and_aggregate_output(tmp_path: Path) -> None:
    source = tmp_path / "source.csv"
    review = tmp_path / "review.csv"
    write_csv(source, [
        {"email_id": "a", "true_priority": "URGENT", "true_needs_response": "yes",
         "true_category": "JOB", "true_is_spam": "no"},
        {"email_id": "b", "true_priority": "INFORMATIONAL", "true_needs_response": "no",
         "true_category": "UPDATES", "true_is_spam": "no"},
        {"email_id": "c", "true_priority": "SPAM", "true_needs_response": "no",
         "true_category": "PROMOTIONS", "true_is_spam": "yes"},
    ])
    write_csv(review, [
        {"email_id": "a", "true_priority": "URGENT", "true_needs_response": "yes",
         "true_category": "JOB", "true_is_spam": "no"},
        {"email_id": "b", "true_priority": "URGENT", "true_needs_response": "no",
         "true_category": "UPDATES", "true_is_spam": "no"},
        {"email_id": "c", "true_priority": "", "true_needs_response": "no",
         "true_category": "PROMOTIONS", "true_is_spam": "yes"},
    ])
    report = score_agreement(source, review)
    assert report["tasks"]["priority"]["paired_annotations"] == 2
    assert report["tasks"]["priority"]["cohen_kappa"] is not None
    assert report["tasks"]["needs_response"]["paired_annotations"] == 3
    serialized = json.dumps(report)
    assert '"email_id"' not in serialized
    assert '"a"' not in serialized
