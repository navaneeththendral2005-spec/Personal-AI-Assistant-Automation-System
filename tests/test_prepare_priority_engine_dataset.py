from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from scripts.prepare_priority_engine_dataset import (
    DatasetPreparationError,
    prepare_dataset,
)

SOURCE_HEADERS = [
    "email_id", "sender", "subject", "snippet", "body_text", "labels",
    "true_is_spam", "true_priority", "true_needs_response", "true_category",
    "predicted_is_spam", "predicted_priority", "predicted_needs_response", "predicted_category",
    "rules_is_spam", "rules_priority", "rules_needs_response", "rules_category",
    "llm_is_spam", "llm_priority", "llm_needs_response", "llm_category",
]
ADJ_HEADERS = [
    "email_id", "final_true_is_spam", "final_true_priority",
    "final_true_needs_response", "final_true_category", "decision_reason",
]


def source_row(email_id: str, **overrides: str) -> dict[str, str]:
    row = {column: "" for column in SOURCE_HEADERS}
    row.update({
        "email_id": email_id,
        "sender": "sender@example.test",
        "subject": "Private subject",
        "snippet": "Private snippet",
        "body_text": "Private body",
        "labels": "",
        "true_is_spam": "no",
        "true_priority": "INFORMATIONAL",
        "true_needs_response": "no",
        "true_category": "OTHER",
        "predicted_is_spam": "no",
        "predicted_priority": "URGENT",
        "predicted_needs_response": "yes",
        "predicted_category": "JOB",
        "rules_is_spam": "no",
        "rules_priority": "INFORMATIONAL",
        "rules_needs_response": "no",
        "rules_category": "OTHER",
        "llm_is_spam": "no",
        "llm_priority": "URGENT",
        "llm_needs_response": "yes",
        "llm_category": "JOB",
    })
    row.update(overrides)
    return row


def adjudication_row(email_id: str = "a", **overrides: str) -> dict[str, str]:
    row = {
        "email_id": email_id,
        "final_true_is_spam": "no",
        "final_true_priority": "NEEDS_REPLY",
        "final_true_needs_response": "yes",
        "final_true_category": "JOB",
        "decision_reason": "Human adjudication rationale.",
    }
    row.update(overrides)
    return row


def write_csv(path: Path, headers: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers, extrasaction="raise")
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def setup_files(tmp_path: Path, source_rows=None, adjudication_rows=None, manifest=None):
    source = tmp_path / "data" / "email_labels.csv"
    adjudication = tmp_path / "data" / "label_disagreement_adjudication_final.csv"
    output = tmp_path / "data" / "email_labels_evaluation.csv"
    split = tmp_path / "data" / "priority_engine_split.json"
    write_csv(source, SOURCE_HEADERS, source_rows or [source_row("a"), source_row("b")])
    write_csv(adjudication, ADJ_HEADERS, adjudication_rows or [adjudication_row()])
    if manifest is not None:
        split.write_text(json.dumps(manifest), encoding="utf-8")
    return source, adjudication, output, split


def test_final_adjudication_overrides_truth_but_not_predictions(tmp_path: Path) -> None:
    source, adjudication, output, split = setup_files(tmp_path)
    source_before = source.read_bytes()
    summary = prepare_dataset(source, adjudication, output, split)
    rows = {row["email_id"]: row for row in read_csv(output)}

    assert rows["a"]["true_priority"] == "NEEDS_REPLY"
    assert rows["a"]["true_is_spam"] == "no"
    assert rows["a"]["true_needs_response"] == "yes"
    assert rows["a"]["true_category"] == "JOB"
    assert rows["a"]["predicted_priority"] == "URGENT"
    assert rows["a"]["llm_priority"] == "URGENT"
    assert rows["b"]["true_priority"] == "INFORMATIONAL"
    assert source.read_bytes() == source_before
    assert summary["unique_adjudicated_emails_matched"] == 1
    assert summary["frozen_manifest"]["status"] == "missing_not_created"
    assert not split.exists()


def test_spam_adjudication_clears_priority(tmp_path: Path) -> None:
    source, adjudication, output, split = setup_files(
        tmp_path,
        adjudication_rows=[adjudication_row(
            final_true_is_spam="yes", final_true_priority="",
            final_true_needs_response="no", final_true_category="OTHER",
        )],
    )
    prepare_dataset(source, adjudication, output, split)
    row = read_csv(output)[0]
    assert row["true_is_spam"] == "yes"
    assert row["true_priority"] == ""


def test_existing_output_is_never_overwritten(tmp_path: Path) -> None:
    source, adjudication, output, split = setup_files(tmp_path)
    output.write_text("keep me", encoding="utf-8")
    with pytest.raises(DatasetPreparationError, match="already exists"):
        prepare_dataset(source, adjudication, output, split)
    assert output.read_text(encoding="utf-8") == "keep me"


def test_unknown_adjudication_id_is_rejected_without_output(tmp_path: Path) -> None:
    source, adjudication, output, split = setup_files(
        tmp_path, adjudication_rows=[adjudication_row("not-in-source")]
    )
    with pytest.raises(DatasetPreparationError, match="absent from the source"):
        prepare_dataset(source, adjudication, output, split)
    assert not output.exists()


def test_duplicate_source_email_ids_are_rejected(tmp_path: Path) -> None:
    source, adjudication, output, split = setup_files(
        tmp_path, source_rows=[source_row("a"), source_row("a")]
    )
    with pytest.raises(DatasetPreparationError, match="duplicate email IDs"):
        prepare_dataset(source, adjudication, output, split)
    assert not output.exists()


def test_spam_priority_conflict_is_rejected(tmp_path: Path) -> None:
    source, adjudication, output, split = setup_files(
        tmp_path, adjudication_rows=[adjudication_row(final_true_is_spam="yes")]
    )
    with pytest.raises(DatasetPreparationError, match="also supplies a priority"):
        prepare_dataset(source, adjudication, output, split)
    assert not output.exists()


def test_invalid_final_label_is_rejected(tmp_path: Path) -> None:
    source, adjudication, output, split = setup_files(
        tmp_path, adjudication_rows=[adjudication_row(final_true_category="NOT_A_CATEGORY")]
    )
    with pytest.raises(DatasetPreparationError, match="invalid or missing final_true_category"):
        prepare_dataset(source, adjudication, output, split)
    assert not output.exists()


def test_manifest_is_checked_but_never_modified(tmp_path: Path) -> None:
    manifest = {
        "version": 1,
        "tasks": {
            "priority": {
                "truth_column": "true_priority",
                "labels": ["URGENT", "NEEDS_REPLY", "INFORMATIONAL", "SPAM"],
                "test_ids": ["b"],
            }
        },
    }
    source, adjudication, output, split = setup_files(tmp_path, manifest=manifest)
    before = split.read_bytes()
    summary = prepare_dataset(source, adjudication, output, split)
    assert summary["frozen_manifest"]["status"] == "passed"
    assert summary["frozen_manifest"]["tasks_checked"] == 1
    assert split.read_bytes() == before


def test_manifest_with_missing_frozen_row_fails_closed(tmp_path: Path) -> None:
    manifest = {
        "version": 1,
        "tasks": {
            "priority": {
                "truth_column": "true_priority",
                "labels": ["URGENT", "NEEDS_REPLY", "INFORMATIONAL", "SPAM"],
                "test_ids": ["gone"],
            }
        },
    }
    source, adjudication, output, split = setup_files(tmp_path, manifest=manifest)
    before = split.read_bytes()
    with pytest.raises(DatasetPreparationError, match=r"row\(s\) missing"):
        prepare_dataset(source, adjudication, output, split)
    assert not output.exists()
    assert split.read_bytes() == before


def test_manifest_with_unverified_frozen_truth_fails_closed(tmp_path: Path) -> None:
    manifest = {
        "version": 1,
        "tasks": {
            "priority": {
                "truth_column": "true_priority",
                "labels": ["URGENT", "NEEDS_REPLY", "INFORMATIONAL", "SPAM"],
                "test_ids": ["b"],
            }
        },
    }
    source, adjudication, output, split = setup_files(
        tmp_path,
        source_rows=[source_row("a"), source_row("b", true_priority="")],
        manifest=manifest,
    )
    with pytest.raises(DatasetPreparationError, match="without valid ground truth"):
        prepare_dataset(source, adjudication, output, split)
    assert not output.exists()


def test_source_schema_is_validated(tmp_path: Path) -> None:
    source, adjudication, output, split = setup_files(tmp_path)
    rows = read_csv(source)
    write_csv(source, [column for column in SOURCE_HEADERS if column != "body_text"], [{key: value for key, value in row.items() if key != "body_text"} for row in rows])
    with pytest.raises(DatasetPreparationError, match="body_text"):
        prepare_dataset(source, adjudication, output, split)
    assert not output.exists()


def test_report_summary_does_not_include_email_identifiers_or_content(tmp_path: Path) -> None:
    source, adjudication, output, split = setup_files(tmp_path)
    summary = prepare_dataset(source, adjudication, output, split)
    rendered = json.dumps(summary)
    for private_value in ("sender@example.test", "Private subject", "Private body", '"email_id"', '"a"'):
        assert private_value not in rendered
