"""Suggest task-specific, non-frozen candidate emails for human labeling.

Examples (run from the repository root):
    python -m scripts.suggest_labeling_batch --task priority
    python -m scripts.suggest_labeling_batch --task category --output data/labeling_queue_category.csv

Queue entries are candidates, never labels. The sampler independently checks
whether the requested task is blank, so an existing label for another task does
not disqualify an otherwise useful email. Every task's frozen test IDs are
excluded using the existing manifest, which is read-only.
"""
from __future__ import annotations

import argparse
import csv
import shutil
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.services.email_features import extract_features, parse_sender
from scripts.evaluate_priority_engine import _norm
from scripts.sample_test_v2 import read_frozen_test_ids

PROJECT_ROOT = Path.cwd()
DEFAULT_INPUT = (
    PROJECT_ROOT / "data" / "email_labels_evaluation_priority_batch1.csv"
)
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "labeling_queue_minority.csv"
DEFAULT_SPLIT_MANIFEST = PROJECT_ROOT / "data" / "priority_engine_split.json"

PRIORITY_CLASSES: tuple[tuple[str, str, str], ...] = (
    ("URGENT", "true_priority", "URGENT"),
    ("NEEDS_REPLY", "true_priority", "NEEDS_REPLY"),
)

# Frozen v2.1 category taxonomy. PERSONAL is a legacy v2.0-draft value
# and is eligible only for explicit human re-review into OTHER.
CATEGORY_CLASSES: tuple[tuple[str, str, str], ...] = (
    ("JOB", "true_category", "JOB"),
    ("FINANCE", "true_category", "FINANCE"),
    ("PROMOTIONS", "true_category", "PROMOTIONS"),
    ("UPDATES", "true_category", "UPDATES"),
    ("OTHER", "true_category", "OTHER"),
)

FOCUS_CLASSES = PRIORITY_CLASSES + CATEGORY_CLASSES
DEFAULT_TARGET = 20
DEFAULT_MAX_PER_CLASS = 15
DEFAULT_MAX_PER_DOMAIN = 3


class SuggestionError(ValueError):
    """A safe-to-display input or output problem."""


def _score(
    focus: str,
    f: dict[str, float],
    row: dict[str, str] | None = None,
) -> float:
    """Rank candidates heuristically; the score is not a predicted label."""
    bulk = (
        f["sender_is_noreply"]
        + f["sender_bulk_local"]
        + f["sender_bulk_domain"]
        + f["body_has_unsubscribe"]
    )
    human = (
        (1.0 - min(bulk, 1.0))
        * (1.0 + f["sender_is_freemail"] + f["body_personal_greeting"])
    )

    if focus == "NEEDS_REPLY":
        return (
            1.5 * f["hits_request"]
            + f["body_question_count"] / 3
            + f["subject_has_question"]
            + 1.5 * f["is_reply"]
            + f["body_has_quoted_reply"]
            + human
            - 2.0 * bulk
        )

    if focus == "URGENT":
        return (
            2.0 * f["hits_urgency"]
            + 2.0 * f["hits_deadline"]
            + f["hits_security"]
            + 2.5 * f["subject_upper_ratio"]
        )

    if focus == "JOB":
        return (
            2.5 * f["hits_job"]
            + 0.5 * f["body_personal_greeting"]
            - 1.0 * f["hits_promo"]
            - 0.5 * f["hits_finance"]
        )

    if focus == "FINANCE":
        return (
            2.0 * f["hits_finance"]
            + 5.0 * f["body_digit_ratio"]
            + 0.5 * f["hits_security"]
            - 0.5 * f["hits_promo"]
        )

    if focus == "PROMOTIONS":
        return (
            2.5 * f["hits_promo"]
            + 1.5 * bulk
            - 1.0 * f["hits_job"]
            - 0.75 * f["hits_finance"]
        )

    if focus == "UPDATES":
        text = " ".join(
            ((row or {}).get(key) or "")
            for key in ("subject", "snippet", "body_text")
        ).lower()
        service_terms = (
            "account",
            "security alert",
            "statement",
            "delivery",
            "order status",
            "verification",
            "password",
            "login",
            "receipt",
            "shipment",
            "service",
            "activity",
            "ticket",
            "renewal",
            "order confirmation",
            "confirmation",
            "dispatched",
            "out for delivery",
            "delivered",
            "subscription",
            "billing update",
            "payment received",
            "booking",
            "reservation",
            "status update",
            "case update",
        )
        service_hits = sum(text.count(term) for term in service_terms)
        return (
            1.5 * service_hits
            + 1.5 * bulk
            - 1.0 * f["hits_promo"]
            - 0.5 * f["hits_request"]
        )

    if focus == "OTHER":
        signals = (
            f["hits_finance"]
            + f["hits_job"]
            + f["hits_promo"]
            + f["hits_request"]
            + f["hits_urgency"]
        )
        return 2.0 - signals - bulk

    raise ValueError(f"Unknown focus class {focus!r}")


def _read(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    if not path.is_file():
        raise SuggestionError("Input dataset not found.")
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            headers = list(reader.fieldnames or [])
            rows = [
                {key: (value or "") for key, value in row.items() if key is not None}
                for row in reader
            ]
    except (OSError, csv.Error) as exc:
        raise SuggestionError(
            f"Could not read dataset ({type(exc).__name__})."
        ) from exc

    if "email_id" not in headers:
        raise SuggestionError("Input CSV must contain an email_id column.")

    ids = [(row.get("email_id") or "").strip() for row in rows]
    if any(not email_id for email_id in ids) or len(ids) != len(set(ids)):
        raise SuggestionError(
            "Input dataset contains missing or duplicate email identifiers."
        )
    return headers, rows


def _load_frozen(path: Path) -> set[str]:
    try:
        return read_frozen_test_ids(path)
    except (FileNotFoundError, ValueError) as exc:
        raise SuggestionError(
            "Existing frozen split manifest is missing or invalid; "
            "no queue was generated."
        ) from exc


def _task_eligible(row: dict[str, str], task: str) -> bool:
    if task == "priority":
        # Spam is evaluated separately and must never receive a priority label.
        if _norm(row.get("true_is_spam"), "is_spam") == "yes":
            return False
        return not (row.get("true_priority") or "").strip()

    if task == "category":
        # PERSONAL is a legacy v2.0-draft value, not a v2.1 category.
        # It is eligible only for explicit human re-review into v2.1 OTHER.
        current = _norm(row.get("true_category"), "category")
        return not (row.get("true_category") or "").strip() or current == "PERSONAL"

    raise ValueError(f"Unknown task {task!r}")


def suggest_batch(
    rows: list[dict[str, str]],
    *,
    task: str = "priority",
    frozen_ids: set[str] | None = None,
    target_per_class: int = DEFAULT_TARGET,
    max_per_class: int = DEFAULT_MAX_PER_CLASS,
    max_per_domain: int = DEFAULT_MAX_PER_DOMAIN,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, int]]]:
    """Return a task-scoped candidate queue and aggregate class summary."""
    if task not in {"priority", "category"}:
        raise SuggestionError("Task must be priority or category.")

    if min(target_per_class, max_per_class, max_per_domain) < 0 or max_per_domain == 0:
        raise SuggestionError(
            "Targets and caps must be non-negative; domain cap must be positive."
        )

    classes = PRIORITY_CLASSES if task == "priority" else CATEGORY_CLASSES
    truth_task = "priority" if task == "priority" else "category"
    frozen = frozen_ids or set()

    eligible_rows = [
        row
        for row in rows
        if (row.get("email_id") or "").strip()
        and (row.get("email_id") or "").strip() not in frozen
        and _task_eligible(row, task)
    ]

    # Count only usable, non-frozen human labels.
    # Frozen evaluation records must never inflate training targets.
    # Spam labels are excluded from priority-class training counts.
    existing = {
        focus: sum(
            1
            for row in rows
            if (row.get("email_id") or "").strip() not in frozen
            and _norm(row.get(column), truth_task) == value
            and not (
                task == "priority"
                and _norm(row.get("true_is_spam"), "is_spam") == "yes"
            )
        )
        for focus, column, value in classes
    }

    feature_by_id = {
        (row["email_id"] or "").strip(): extract_features(row)
        for row in eligible_rows
    }
    row_by_id = {
        (row["email_id"] or "").strip(): row
        for row in eligible_rows
    }
    domain_by_id = {
        (row["email_id"] or "").strip():
            parse_sender(row.get("sender")).registered_domain or "(unknown)"
        for row in eligible_rows
    }

    queue: list[dict[str, Any]] = []
    summary: dict[str, dict[str, int]] = {}
    used: set[str] = set()

    for focus, _column, _value in classes:
        wanted = min(max_per_class, max(0, target_per_class - existing[focus]))
        summary[focus] = {
            "verified_now": existing[focus],
            "requested": wanted,
            "suggested": 0,
        }

        ranked = sorted(
            (
                email_id
                for email_id in feature_by_id
                if email_id not in used
                and not (
                    task == "category"
                    and _norm(
                        row_by_id[email_id].get("true_category"), "category"
                    ) == "PERSONAL"
                    and focus != "OTHER"
                )
            ),
            key=lambda email_id: (
                -_score(focus, feature_by_id[email_id], row_by_id[email_id]),
                email_id,
            ),
        )

        per_domain: defaultdict[str, int] = defaultdict(int)

        for email_id in ranked:
            if summary[focus]["suggested"] >= wanted:
                break

            domain = domain_by_id[email_id]
            if per_domain[domain] >= max_per_domain:
                continue

            per_domain[domain] += 1
            used.add(email_id)
            score = _score(focus, feature_by_id[email_id], row_by_id[email_id])
            summary[focus]["suggested"] += 1

            queue.append(
                {
                    "email_id": email_id,
                    "review_reason": f"minority_class_candidate:{focus}",
                    "focus_task": task,
                    "focus_class": focus,
                    "candidate_score": round(score, 3),
                }
            )

    return queue, summary


def write_queue(
    queue: list[dict[str, Any]],
    output_path: Path,
    *,
    overwrite: bool = False,
) -> Path | None:
    if output_path.exists() and not overwrite:
        raise SuggestionError(
            "Output already exists. Pass --overwrite to replace it; "
            "a backup will be created."
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    backup_path = None

    if output_path.exists():
        backup_dir = output_path.parent.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup_path = (
            backup_dir / f"{output_path.stem}.{stamp}{output_path.suffix}"
        )
        shutil.copy2(output_path, backup_path)

    fields = [
        "email_id",
        "review_reason",
        "focus_task",
        "focus_class",
        "candidate_score",
    ]
    temp = output_path.with_suffix(output_path.suffix + ".tmp")

    try:
        with temp.open("x", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=fields,
                lineterminator="\n",
            )
            writer.writeheader()
            writer.writerows(queue)
        temp.replace(output_path)
    finally:
        if temp.exists():
            temp.unlink()

    return backup_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--split-manifest",
        type=Path,
        default=DEFAULT_SPLIT_MANIFEST,
    )
    parser.add_argument(
        "--task",
        choices=("priority", "category"),
        default="priority",
    )
    parser.add_argument(
        "--target-per-class",
        type=int,
        default=DEFAULT_TARGET,
    )
    parser.add_argument(
        "--max-per-class",
        type=int,
        default=DEFAULT_MAX_PER_CLASS,
    )
    parser.add_argument(
        "--max-per-domain",
        type=int,
        default=DEFAULT_MAX_PER_DOMAIN,
    )
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)

    try:
        _, rows = _read(args.input)
        frozen = _load_frozen(args.split_manifest)
        queue, summary = suggest_batch(
            rows,
            task=args.task,
            frozen_ids=frozen,
            target_per_class=args.target_per_class,
            max_per_class=args.max_per_class,
            max_per_domain=args.max_per_domain,
        )
        write_queue(queue, args.output, overwrite=args.overwrite)
    except SuggestionError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    print(f"Task: {args.task}; frozen IDs excluded: {len(frozen)}")
    print(
        f"Wrote {len(queue)} candidate rows to {args.output}; "
        "no labels were assigned."
    )
    for focus, info in summary.items():
        print(
            f"- {focus}: verified {info['verified_now']}, "
            f"requested {info['requested']}, "
            f"suggested {info['suggested']}"
        )
    print(
        "Queue contains identifiers and heuristic scores only; "
        "no email content is exported."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
