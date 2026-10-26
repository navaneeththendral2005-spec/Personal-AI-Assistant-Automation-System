from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.priority_engine_splits import load_or_create_test_ids, row_key


def _load(rows, manifest, **overrides):
    options = {
        "task": "priority",
        "truth_column": "true_priority",
        "labels": ("URGENT", "INFORMATIONAL"),
        "manifest_path": manifest,
        "test_fraction": 0.25,
        "seed": 42,
    }
    options.update(overrides)
    return load_or_create_test_ids(rows, **options)


def test_existing_frozen_ids_are_preserved_when_dataset_grows(tmp_path):
    manifest = tmp_path / "split.json"
    rows = [{"email_id": f"id-{i}", "true_priority": "URGENT" if i % 2 else "INFORMATIONAL"} for i in range(20)]
    original, created = _load(rows, manifest)
    assert created and original

    expanded = rows + [{"email_id": "new-id", "true_priority": "URGENT"}]
    current, created_again = _load(expanded, manifest)
    assert not created_again
    assert current == original
    assert "new-id" not in current


@pytest.mark.parametrize("fraction", [0, 1, -0.1, 1.1, float("nan"), float("inf"), True])
def test_invalid_test_fraction_is_rejected_before_manifest_creation(tmp_path, fraction):
    manifest = tmp_path / "split.json"
    with pytest.raises(ValueError, match="test_fraction"):
        _load([], manifest, test_fraction=fraction)
    assert not manifest.exists()


def test_duplicate_verified_row_keys_are_rejected(tmp_path):
    rows = [
        {"email_id": "duplicate-id", "true_priority": "URGENT"},
        {"email_id": "duplicate-id", "true_priority": "INFORMATIONAL"},
    ]
    with pytest.raises(ValueError, match="Duplicate row identifiers"):
        _load(rows, tmp_path / "split.json")


@pytest.mark.parametrize("task_entry", [None, [], {}, {"test_ids": ["id-1", "id-1"]}, {"test_ids": [""]}, {"test_ids": [1]}])
def test_malformed_existing_task_entry_is_rejected(tmp_path, task_entry):
    manifest = tmp_path / "split.json"
    manifest.write_text(json.dumps({"version": 1, "tasks": {"priority": task_entry}}), encoding="utf-8")
    with pytest.raises(ValueError, match="task entry|test_ids"):
        _load([], manifest)


def test_existing_manifest_schema_mismatch_is_rejected_without_rewriting_ids(tmp_path):
    manifest = tmp_path / "split.json"
    original = {"version": 1, "tasks": {"priority": {"truth_column": "old_truth", "labels": ["URGENT", "INFORMATIONAL"], "test_ids": ["frozen-id"]}}}
    manifest.write_text(json.dumps(original), encoding="utf-8")
    with pytest.raises(ValueError, match="Truth column mismatch"):
        _load([], manifest)
    assert json.loads(manifest.read_text(encoding="utf-8")) == original


def test_fallback_row_key_does_not_expose_content():
    key = row_key({"subject": "private subject", "body_text": "secret body"})
    assert key.startswith("sha256:")
    assert "private subject" not in key
    assert "secret body" not in key

def test_duplicate_verified_row_keys_are_rejected_when_manifest_exists(tmp_path):
    manifest = tmp_path / "split.json"

    rows = [
        {"email_id": f"id-{i}", "true_priority": "URGENT" if i % 2 else "INFORMATIONAL"}
        for i in range(20)
    ]

    _load(rows, manifest)

    duplicate_rows = rows + [
        {"email_id": "id-0", "true_priority": "URGENT"}
    ]

    with pytest.raises(ValueError, match="Duplicate row identifiers"):
        _load(duplicate_rows, manifest)


def test_frozen_ids_are_not_changed_when_source_row_disappears(tmp_path):
    manifest = tmp_path / "split.json"

    rows = [
        {"email_id": f"id-{i}", "true_priority": "URGENT" if i % 2 else "INFORMATIONAL"}
        for i in range(20)
    ]

    original_ids, created = _load(rows, manifest)
    assert created
    assert original_ids

    remaining_rows = [
        row for row in rows
        if row["email_id"] not in original_ids
    ]

    current_ids, created_again = _load(remaining_rows, manifest)

    assert not created_again
    assert current_ids == original_ids
