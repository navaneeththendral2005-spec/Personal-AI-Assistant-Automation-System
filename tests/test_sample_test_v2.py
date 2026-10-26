from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from scripts.sample_test_v2 import sample_test_v2


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
    path.write_text(
        json.dumps({
            "version": 1,
            "tasks": {
                "priority": {"test_ids": frozen},
                "category": {"test_ids": frozen[:1]},
            },
        }),
        encoding="utf-8",
    )


def fixture_data(tmp_path: Path) -> tuple[Path, Path]:
    source = tmp_path / "labels.csv"
    rows = []

    for i in range(100):
        row = {
            "email_id": f"id-{i:03d}",
            "sender": f"sender{i}@example.test",
            "subject": f"Synthetic subject {i}",
            "snippet": "Synthetic snippet",
            "body_text": "Synthetic body only",
            "labels": "INBOX",
            "true_is_spam": "",
            "true_priority": "",
            "true_needs_response": "",
            "true_category": "",
        }

        if i < 10:
            row["true_priority"] = "URGENT"

        rows.append(row)

    write_csv(source, rows)

    split = tmp_path / "priority_engine_split.json"
    make_split(split, ["id-010", "id-011", "id-012"])

    return source, split


# ---------------------------------------------------------------------------
# Original tests
# ---------------------------------------------------------------------------

def test_sample_is_seeded_unique_and_excludes_verified_and_frozen(
    tmp_path: Path,
) -> None:
    source, split = fixture_data(tmp_path)
    manifest = tmp_path / "test_v2_manifest.json"
    output = tmp_path / "test_v2_labels.csv"

    result = sample_test_v2(
        source,
        split_manifest_path=split,
        manifest_path=manifest,
        output_path=output,
        sample_size=60,
        seed=17,
    )

    ids = json.loads(manifest.read_text(encoding="utf-8"))["email_ids"]

    assert len(ids) == len(set(ids)) == 60
    assert not set(ids) & {f"id-{i:03d}" for i in range(10)}
    assert not set(ids) & {"id-010", "id-011", "id-012"}
    assert result["sample_size"] == 60

    rows = read_csv(output)

    assert {row["email_id"] for row in rows} == set(ids)
    assert all(not row["true_is_spam"].strip() for row in rows)
    assert all(not row["true_priority"].strip() for row in rows)
    assert all(not row["true_needs_response"].strip() for row in rows)
    assert all(not row["true_category"].strip() for row in rows)


def test_sample_resume_preserves_annotations_and_manifest(
    tmp_path: Path,
) -> None:
    source, split = fixture_data(tmp_path)
    manifest = tmp_path / "test_v2_manifest.json"
    output = tmp_path / "test_v2_labels.csv"

    sample_test_v2(
        source,
        split_manifest_path=split,
        manifest_path=manifest,
        output_path=output,
        sample_size=60,
        seed=17,
    )

    original_manifest = manifest.read_bytes()
    rows = read_csv(output)
    rows[0]["true_priority"] = "URGENT"
    write_csv(output, rows)
    before = output.read_bytes()

    result = sample_test_v2(
        source,
        split_manifest_path=split,
        manifest_path=manifest,
        output_path=output,
        sample_size=60,
        seed=999,
    )

    assert manifest.read_bytes() == original_manifest
    assert output.read_bytes() == before
    assert result["manifest_created"] is False
    assert result["annotation_file_created"] is False


def test_existing_manifest_size_mismatch_fails_without_overwriting(
    tmp_path: Path,
) -> None:
    source, split = fixture_data(tmp_path)
    manifest = tmp_path / "test_v2_manifest.json"
    output = tmp_path / "test_v2_labels.csv"

    manifest.write_text(
        json.dumps({
            "version": 1,
            "email_ids": ["id-020", "id-021"],
        }),
        encoding="utf-8",
    )
    before = manifest.read_bytes()

    with pytest.raises(ValueError, match="sample-size"):
        sample_test_v2(
            source,
            split_manifest_path=split,
            manifest_path=manifest,
            output_path=output,
            sample_size=60,
        )

    assert manifest.read_bytes() == before
    assert not output.exists()


def test_duplicate_source_ids_are_rejected(tmp_path: Path) -> None:
    source = tmp_path / "labels.csv"

    write_csv(source, [
        {"email_id": "duplicate", "true_priority": ""},
        {"email_id": "duplicate", "true_priority": ""},
    ])

    split = tmp_path / "split.json"
    make_split(split, [])

    with pytest.raises(ValueError, match="duplicate email_id"):
        sample_test_v2(
            source,
            split_manifest_path=split,
            manifest_path=tmp_path / "manifest.json",
            output_path=tmp_path / "out.csv",
            sample_size=1,
        )


def test_insufficient_candidates_fails_cleanly(tmp_path: Path) -> None:
    source = tmp_path / "labels.csv"
    write_csv(source, [{"email_id": "only-one", "true_priority": ""}])

    split = tmp_path / "split.json"
    make_split(split, [])

    with pytest.raises(ValueError, match="Only 1 eligible"):
        sample_test_v2(
            source,
            split_manifest_path=split,
            manifest_path=tmp_path / "manifest.json",
            output_path=tmp_path / "out.csv",
            sample_size=2,
        )


# ---------------------------------------------------------------------------
# Additional manifest-integrity tests
# ---------------------------------------------------------------------------

def test_manifest_with_duplicate_ids_is_rejected(tmp_path: Path) -> None:
    source, split = fixture_data(tmp_path)
    manifest = tmp_path / "manifest.json"
    output = tmp_path / "review.csv"

    manifest.write_text(
        json.dumps({
            "version": 1,
            "sample_size": 2,
            "email_ids": ["id-020", "id-020"],
        }),
        encoding="utf-8",
    )
    original_manifest = manifest.read_bytes()

    with pytest.raises(ValueError, match="unique string email IDs"):
        sample_test_v2(
            source,
            split_manifest_path=split,
            manifest_path=manifest,
            output_path=output,
            sample_size=2,
        )

    assert manifest.read_bytes() == original_manifest
    assert not output.exists()


def test_manifest_with_missing_source_ids_is_rejected(
    tmp_path: Path,
) -> None:
    source, split = fixture_data(tmp_path)
    manifest = tmp_path / "manifest.json"
    output = tmp_path / "review.csv"

    manifest.write_text(
        json.dumps({
            "version": 1,
            "sample_size": 2,
            "email_ids": ["id-020", "missing-id"],
        }),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="missing from the input CSV"):
        sample_test_v2(
            source,
            split_manifest_path=split,
            manifest_path=manifest,
            output_path=output,
            sample_size=2,
        )

    assert not output.exists()


def test_manifest_overlapping_frozen_ids_is_rejected(
    tmp_path: Path,
) -> None:
    source, split = fixture_data(tmp_path)
    manifest = tmp_path / "manifest.json"
    output = tmp_path / "review.csv"
