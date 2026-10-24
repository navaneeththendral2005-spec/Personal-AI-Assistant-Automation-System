"""Private, persistent holdout assignments for FRIDAY evaluation.

The manifest stores only opaque email identifiers and task-specific assignments.
Keep it under data/ (which is git-ignored); never publish it with reports.
Newly verified rows not present in an existing manifest are assigned to development,
never silently added to the frozen test set.
"""
from __future__ import annotations

import hashlib
import json
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
    """Return frozen test IDs and whether this invocation created the manifest."""
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
        if manifest.get("version") != MANIFEST_VERSION or not isinstance(manifest.get("tasks"), dict):
            raise ValueError(f"Unsupported or malformed split manifest: {manifest_path}")

    tasks = manifest.setdefault("tasks", {})
    if task in tasks:
        test_ids = tasks[task].get("test_ids")
        if not isinstance(test_ids, list) or not all(isinstance(item, str) for item in test_ids):
            raise ValueError(f"Malformed test_ids for task {task!r} in {manifest_path}")
        return set(test_ids), False

    # New task assignment: stratified where possible. This assignment is then frozen.
    verified: list[tuple[str, str]] = []
    allowed = {label.lower() if task == "needs_response" else label.upper() for label in labels}
    for row in rows:
        truth = (row.get(truth_column) or "").strip()
        normalized = truth.lower() if task == "needs_response" else truth.upper()
        if normalized in allowed:
            verified.append((row_key(row), normalized))
    if len(verified) < 2:
        test_ids: set[str] = set()
    else:
        from collections import Counter
        from sklearn.model_selection import train_test_split
        counts = Counter(label for _, label in verified)
        represented = [label for label in labels if counts.get(label.lower() if task == "needs_response" else label.upper(), 0)]
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
