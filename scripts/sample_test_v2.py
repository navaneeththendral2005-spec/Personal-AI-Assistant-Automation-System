"""Create a private, reproducible test_v2 sample without touching existing splits.

Example (from the project root):
    python -m scripts.sample_test_v2 --input data/email_labels.csv \
        --split-manifest data/priority_engine_split.json \
        --manifest data/test_v2_manifest.json --output data/test_v2_labels.csv \
        --sample-size 60 --seed 20261026

The manifest contains IDs only. The separate private review CSV contains only the
selected email context and blank human-label fields, so it can be labeled locally.
An existing manifest is immutable: reruns reuse its IDs and never resample.
"""
from __future__ import annotations

import argparse
import csv
import json
import random
from pathlib import Path
from typing import Any

TRUTH_FIELDS = (
    "true_is_spam",
    "true_priority",
    "true_needs_response",
    "true_category",
)
CONTEXT_FIELDS = ("email_id", "sender", "subject", "snippet", "body_text", "labels")
MANIFEST_VERSION = 1


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    if not path.is_file():
        raise FileNotFoundError(f"Input CSV not found: {path}")
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or "email_id" not in reader.fieldnames:
            raise ValueError(f"{path} must contain an email_id column")
        return list(reader.fieldnames), [dict(row) for row in reader]


def read_frozen_test_ids(path: Path) -> set[str]:
    """Read the union of every task's frozen IDs without changing the manifest."""
    if not path.is_file():
        raise FileNotFoundError(f"Existing frozen split manifest not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Frozen split manifest is unreadable: {path}") from exc
    tasks = data.get("tasks")
    if data.get("version") != 1 or not isinstance(tasks, dict):
        raise ValueError(f"Unsupported or malformed frozen split manifest: {path}")
    frozen: set[str] = set()
    for task_name, task_data in tasks.items():
        if not isinstance(task_data, dict):
            raise ValueError(f"Malformed task entry {task_name!r} in {path}")
        ids = task_data.get("test_ids")
        if not isinstance(ids, list) or not all(isinstance(value, str) for value in ids):
            raise ValueError(f"Malformed test_ids for task {task_name!r} in {path}")
        frozen.update(value.strip() for value in ids if value.strip())
    return frozen


def has_any_human_label(row: dict[str, str]) -> bool:
    """Treat any existing non-empty true_* field as already verified."""
    return any((row.get(field) or "").strip() for field in TRUTH_FIELDS)


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    try:
        temp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temp.replace(path)
    finally:
        if temp.exists():
            temp.unlink()


def _write_review_csv(path: Path, source_by_id: dict[str, dict[str, str]], ids: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(CONTEXT_FIELDS) + list(TRUTH_FIELDS)
    temp = path.with_suffix(path.suffix + ".tmp")
    try:
        with temp.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            for email_id in ids:
                source = source_by_id[email_id]
                row = {field: (source.get(field) or "") for field in CONTEXT_FIELDS}
                row.update({field: "" for field in TRUTH_FIELDS})
                writer.writerow(row)
        temp.replace(path)
    finally:
        if temp.exists():
            temp.unlink()


def sample_test_v2(
    input_path: Path,
    *,
    split_manifest_path: Path,
    manifest_path: Path,
    output_path: Path,
    sample_size: int = 60,
    seed: int = 20261026,
) -> dict[str, Any]:
    """Create or resume test_v2. Existing manifests and annotations are never overwritten."""
    if sample_size < 1:
        raise ValueError("sample_size must be positive")
    _, rows = read_csv(input_path)
    ids = [(row.get("email_id") or "").strip() for row in rows]
    if any(not email_id for email_id in ids):
        raise ValueError("Every source row must have a non-empty email_id")
    if len(ids) != len(set(ids)):
        raise ValueError("Input CSV contains duplicate email_id values")

    source_by_id = {email_id: row for email_id, row in zip(ids, rows)}
    frozen_ids = read_frozen_test_ids(split_manifest_path)
    verified_ids = {email_id for email_id, row in source_by_id.items() if has_any_human_label(row)}
    excluded_ids = frozen_ids | verified_ids
    eligible_ids = sorted(set(source_by_id) - excluded_ids)

    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"test_v2 manifest is unreadable: {manifest_path}") from exc
        if manifest.get("version") != MANIFEST_VERSION or not isinstance(manifest.get("email_ids"), list):
            raise ValueError(f"Malformed or unsupported test_v2 manifest: {manifest_path}")
        selected_ids = manifest["email_ids"]
        if not all(isinstance(value, str) for value in selected_ids) or len(selected_ids) != len(set(selected_ids)):
            raise ValueError("test_v2 manifest must contain unique string email IDs")
        if len(selected_ids) != sample_size:
            raise ValueError(
                f"Existing manifest has {len(selected_ids)} IDs but --sample-size={sample_size}; "
                "do not silently change a frozen sample. Keep the original size or create a separately named manifest."
            )
        if set(selected_ids) & excluded_ids:
            raise ValueError("Existing test_v2 manifest overlaps verified or frozen IDs; refusing to continue")
        missing = set(selected_ids) - set(source_by_id)
        if missing:
            raise ValueError("Existing test_v2 IDs are missing from the input CSV; refusing to rewrite the manifest")
        created = False
    else:
        if len(eligible_ids) < sample_size:
            raise ValueError(
                f"Only {len(eligible_ids)} eligible unverified emails remain; {sample_size} requested"
            )
        selected_ids = random.Random(seed).sample(eligible_ids, sample_size)
        # Manifest is intentionally IDs-only; no message text or labels are persisted here.
        manifest = {
            "version": MANIFEST_VERSION,
            "sample_name": "test_v2",
            "seed": seed,
            "sample_size": sample_size,
            "email_ids": selected_ids,
            "policy": "Private IDs only. Frozen sample: do not resample, tune on, or replace these IDs.",
        }
        _atomic_write_json(manifest_path, manifest)
        created = True

    selected_set = set(selected_ids)
    if selected_set & frozen_ids:
        raise ValueError("test_v2 overlaps the existing frozen test set; refusing to continue")
    if selected_set & verified_ids:
        raise ValueError("test_v2 contains previously verified IDs; refusing to continue")

    if output_path.exists():
        _, existing_rows = read_csv(output_path)
        existing_ids = [(row.get("email_id") or "").strip() for row in existing_rows]
        if len(existing_ids) != len(set(existing_ids)) or set(existing_ids) != selected_set:
            raise ValueError(
                f"Existing annotation file {output_path} does not match the frozen manifest; "
                "it was not modified. Back it up and investigate manually."
            )
        output_created = False  # Preserve all progress/labels on resume.
    else:
        _write_review_csv(output_path, source_by_id, selected_ids)
        output_created = True

    return {
        "sample_name": "test_v2",
        "sample_size": len(selected_ids),
        "eligible_unverified_count_at_run": len(eligible_ids),
        "excluded_verified_count": len(verified_ids),
        "excluded_frozen_test_id_count": len(frozen_ids),
        "manifest_created": created,
        "annotation_file_created": output_created,
        "manifest_path": str(manifest_path),
        "annotation_path": str(output_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("data/email_labels.csv"))
    parser.add_argument("--split-manifest", type=Path, default=Path("data/priority_engine_split.json"))
    parser.add_argument("--manifest", type=Path, default=Path("data/test_v2_manifest.json"))
    parser.add_argument("--output", type=Path, default=Path("data/test_v2_labels.csv"))
    parser.add_argument("--sample-size", type=int, default=60)
    parser.add_argument("--seed", type=int, default=20261026)
    args = parser.parse_args()
    try:
        result = sample_test_v2(
            args.input,
            split_manifest_path=args.split_manifest,
            manifest_path=args.manifest,
            output_path=args.output,
            sample_size=args.sample_size,
            seed=args.seed,
        )
    except (FileNotFoundError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2))
    print("Private files only. The existing frozen split manifest was read, never modified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
