"""Private, persistent holdout assignments for FRIDAY evaluation.

The manifest stores only opaque email identifiers and task-specific assignments.
Keep it under data/ (which is git-ignored); never publish it with reports.
Newly verified rows not present in an existing manifest are assigned to development,
never silently added to the frozen test set.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

MANIFEST_VERSION = 1


def row_key(row: dict[str, str]) -> str:
    email_id = (row.get("email_id") or "").strip()
    if email_id:
        return email_id
    # Stable fallback for synthetic/legacy CSVs without email IDs; do not persist raw content.
    fields = ("sender", "subject", "snippet", "body_text", "labels")
    material = "\x1f".join((row.get(field) or "").strip() for field in fields)
    return "sha256:" + hashlib.sha256(material.encode("utf-8")).hexdigest()


def load_or_create_test_ids(
    rows: list[dict[str, str]], *, task: str, truth_column: str,
    labels: tuple[str, ...], manifest_path: Path, test_fraction: float, seed: int,
) -> tuple[set[str], bool]:
    """Return frozen test IDs and whether this invocation created the manifest.

    Existing assignments are immutable: validation may reject a malformed or
    incompatible manifest, but never repairs or silently regenerates its IDs.
    """
    if not task or not isinstance(task, str):
        raise ValueError("task must be a non-empty string")
    if not truth_column or not isinstance(truth_column, str):
        raise ValueError("truth_column must be a non-empty string")
    if not labels or any(not isinstance(label, str) or not label.strip() for label in labels):
        raise ValueError("labels must contain non-empty strings")
    if len(set(labels)) != len(labels):
        raise ValueError("labels must be unique")
    if not isinstance(test_fraction, (int, float)) or isinstance(test_fraction, bool) or not math.isfinite(test_fraction) or not 0 < test_fraction < 1:
        raise ValueError("test_fraction must be a finite number strictly between 0 and 1")
    if not isinstance(seed, int) or isinstance(seed, bool): 
        raise ValueError("seed must be an integer") 
    
    allowed_labels = { 
        label.lower() if task in {"needs_response", "is_spam"} else label.upper() 
        for label in labels 
    }

    verified_keys = []
    for row in rows:
        truth = (row.get(truth_column) or "").strip()
        normalized = (
            truth.lower()
            if task in {"needs_response", "is_spam"}
            else truth.upper()
        )
        if normalized in allowed_labels:
            verified_keys.append(row_key(row))

    if len(verified_keys) != len(set(verified_keys)):
        raise ValueError(
            f"Duplicate row identifiers among verified rows for task {task!r}; "
            "each verified row must have a unique email_id or fallback content key."
        )

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {}
    created = not manifest_path.exists()
    if not created:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(
                f"Split manifest {manifest_path} is unreadable. Back it up and repair it; "
                "do not delete it casually because it freezes the test set."
            ) from exc
        if (
            not isinstance(manifest, dict)
            or manifest.get("version") != MANIFEST_VERSION
            or not isinstance(manifest.get("tasks"), dict)
        ):
            raise ValueError(f"Unsupported or malformed split manifest: {manifest_path}")

    tasks = manifest.setdefault("tasks", {})
    if task in tasks:
        task_entry = tasks[task]
        if not isinstance(task_entry, dict):
            raise ValueError(f"Malformed task entry for {task!r} in {manifest_path}")
        test_ids = task_entry.get("test_ids")
        if (
            not isinstance(test_ids, list)
            or not all(isinstance(item, str) and item.strip() for item in test_ids)
            or len(set(test_ids)) != len(test_ids)
        ):
            raise ValueError(f"Malformed test_ids for task {task!r} in {manifest_path}")
        if "truth_column" in task_entry and task_entry["truth_column"] != truth_column:
            raise ValueError(f"Truth column mismatch for task {task!r} in {manifest_path}")
        if "labels" in task_entry and task_entry["labels"] != list(labels):
            raise ValueError(f"Label schema mismatch for task {task!r} in {manifest_path}")
        return set(test_ids), False

    # New task assignment: stratified where possible. This assignment is then frozen.
    verified: list[tuple[str, str]] = []
    allowed = {label.lower() if task in {"needs_response", "is_spam"} else label.upper() for label in labels}
    for row in rows:
        truth = (row.get(truth_column) or "").strip()
        normalized = truth.lower() if task in {"needs_response", "is_spam"} else truth.upper()
        if normalized in allowed:
            verified.append((row_key(row), normalized))
    if len(verified) < 2:
        test_ids: set[str] = set()
    else:
        from collections import Counter
        from sklearn.model_selection import train_test_split
        counts = Counter(label for _, label in verified)
        represented = [label for label in labels if counts.get(label.lower() if task in {"needs_response", "is_spam"} else label.upper(), 0)]
        n_test = max(len(represented), round(len(verified) * test_fraction))
        n_test = min(n_test, len(verified) - 1)
        try:
            _, test_indices = train_test_split(
                list(range(len(verified))), test_size=n_test, random_state=seed,
                stratify=[label for _, label in verified],
            )
        except ValueError:
            # Tiny/rare classes cannot be stratified; seeded deterministic fallback.
            import random
            indices = list(range(len(verified)))
            random.Random(f"{seed}:{task}").shuffle(indices)
            test_indices = indices[:n_test]
        test_ids = {verified[index][0] for index in test_indices}

    tasks[task] = {
        "truth_column": truth_column,
        "labels": list(labels),
        "test_fraction_at_creation": test_fraction,
        "seed_at_creation": seed,
        "test_ids": sorted(test_ids),
        "created_from_verified_rows": len(verified),
        "note": "Frozen test IDs. Newly verified IDs not listed here belong to development, not test.",
    }
    manifest["version"] = MANIFEST_VERSION
    manifest["policy"] = "Private IDs only; never store email content or publish this manifest."
    temporary = manifest_path.with_suffix(manifest_path.suffix + ".tmp")
    try:
        temporary.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        temporary.replace(manifest_path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return test_ids, True
