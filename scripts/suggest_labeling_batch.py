"""Suggest which unlabeled emails to label next to fix class imbalance.

Run from the project root:

    python -m scripts.suggest_labeling_batch
    python -m scripts.label_emails --queue data/labeling_queue_minority.csv

For every under-represented class (FINANCE, NEEDS_REPLY, PERSONAL, OTHER, URGENT) the
script ranks the *unlabeled* emails by how strongly their shared features
(``backend.services.email_features``) look like that class, then writes a queue CSV
that ``scripts.label_emails --queue`` understands.

Important
---------
* These are heuristic CANDIDATES, not labels. A person must still label each email;
  nothing here is ever written to a ``true_*`` column and no prediction is ground truth.
* Queue entries carry only ``email_id``, the focus class, and a score (no content).
* Selection is diversified (at most N emails per sender domain) so one noisy sender
  cannot fill the whole queue.
* An existing output file is never overwritten unless ``--overwrite`` is given.
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from backend.services.email_features import extract_features, parse_sender
from scripts.evaluate_priority_engine import _norm

PROJECT_ROOT = Path.cwd()
DEFAULT_INPUT = PROJECT_ROOT / "data" / "email_labels.csv"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "labeling_queue_minority.csv"

# (focus class, truth column that counts it, normalised label value)
FOCUS_CLASSES: tuple[tuple[str, str, str], ...] = (
    ("NEEDS_REPLY", "true_priority", "NEEDS_REPLY"),
    ("FINANCE", "true_category", "FINANCE"),
    ("PERSONAL", "true_category", "PERSONAL"),
    ("URGENT", "true_priority", "URGENT"),
    ("OTHER", "true_category", "OTHER"),
)
DEFAULT_TARGET = 20
DEFAULT_MAX_PER_CLASS = 15
DEFAULT_MAX_PER_DOMAIN = 3


class SuggestionError(ValueError):
    """A safe-to-display input or output problem."""


def _score(focus: str, f: dict[str, float]) -> float:
    """Heuristic likelihood that an email belongs to ``focus`` (higher = likelier)."""
    bulk = f["sender_is_noreply"] + f["sender_bulk_local"] + f["sender_bulk_domain"] + f["body_has_unsubscribe"]
    human = (1.0 - min(bulk, 1.0)) * (1.0 + f["sender_is_freemail"] + f["body_personal_greeting"])
    if focus == "NEEDS_REPLY":
        return (
            1.5 * f["hits_request"] + 1.0 * f["body_question_count"] / 3 + 1.0 * f["subject_has_question"]
            + 1.5 * f["is_reply"] + 1.0 * f["body_has_quoted_reply"] + 1.0 * human - 2.0 * bulk
        )
    if focus == "FINANCE":
        return 2.0 * f["hits_finance"] + 0.5 * f["body_digit_ratio"] * 10 + 0.5 * f["hits_security"] - 0.5 * f["hits_promo"]
    if focus == "PERSONAL":
        return 2.0 * human + 1.0 * f["body_personal_greeting"] + 0.5 * f["sender_has_display_name"] - 2.0 * bulk - 0.5 * f["hits_promo"]
    if focus == "URGENT":
        return 2.0 * f["hits_urgency"] + 2.0 * f["hits_deadline"] + 1.0 * f["hits_security"] + 0.5 * f["subject_upper_ratio"] * 5
    if focus == "OTHER":
        signals = f["hits_finance"] + f["hits_job"] + f["hits_promo"] + f["hits_request"] + f["hits_urgency"]
        return 2.0 - signals - 1.0 * bulk
    raise ValueError(f"Unknown focus class {focus!r}")


def _read(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    if not path.is_file():
        raise SuggestionError(f"Input dataset not found: {path}")
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            headers = list(reader.fieldnames or [])
            rows = [{k: (v or "") for k, v in row.items() if k is not None} for row in reader]
    except (OSError, csv.Error) as exc:
        raise SuggestionError(f"Could not read dataset ({type(exc).__name__}).") from exc
    if "email_id" not in headers:
        raise SuggestionError("Input CSV must contain an email_id column.")
    return headers, rows


def suggest_batch(
    rows: list[dict[str, str]],
    *,
    target_per_class: int = DEFAULT_TARGET,
    max_per_class: int = DEFAULT_MAX_PER_CLASS,
    max_per_domain: int = DEFAULT_MAX_PER_DOMAIN,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, int]]]:
    """Return (queue rows, per-class summary). Pure function of the rows."""
    truth_columns = ("true_priority", "true_needs_response", "true_category", "true_is_spam")

    existing: dict[str, int] = {}
    for focus, column, value in FOCUS_CLASSES:
        task = "priority" if column == "true_priority" else "category"
        existing[focus] = sum(1 for row in rows if _norm(row.get(column), task) == value)

    unlabeled = [
        row for row in rows
        if (row.get("email_id") or "").strip()
        and not any((row.get(column) or "").strip() for column in truth_columns)
    ]
    features = {row["email_id"].strip(): extract_features(row) for row in unlabeled}
    domains = {
        row["email_id"].strip(): parse_sender(row.get("sender")).registered_domain or "(unknown)"
        for row in unlabeled
    }

    queue: list[dict[str, Any]] = []
    summary: dict[str, dict[str, int]] = {}
    used: set[str] = set()
    for focus, _column, _value in FOCUS_CLASSES:  # scarcest classes claim emails first
        wanted = min(max_per_class, max(0, target_per_class - existing[focus]))
        summary[focus] = {"verified_now": existing[focus], "requested": wanted, "suggested": 0}
        if wanted == 0:
            continue
        ranked = sorted(
            (email_id for email_id in features if email_id not in used),
            key=lambda email_id: (-_score(focus, features[email_id]), email_id),
        )
        per_domain: defaultdict[str, int] = defaultdict(int)
        for email_id in ranked:
            if summary[focus]["suggested"] >= wanted:
                break
            if per_domain[domains[email_id]] >= max_per_domain:
                continue
            per_domain[domains[email_id]] += 1
            used.add(email_id)
            summary[focus]["suggested"] += 1
            queue.append({
                "email_id": email_id,
                "review_reason": f"minority_class_candidate:{focus}",
                "focus_class": focus,
                "candidate_score": round(_score(focus, features[email_id]), 3),
            })
    return queue, summary


def write_queue(queue: list[dict[str, Any]], output_path: Path, *, overwrite: bool = False) -> None:
    if output_path.exists() and not overwrite:
        raise SuggestionError(f"Output already exists: {output_path}. Pass --overwrite to replace it.")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["email_id", "review_reason", "focus_class", "candidate_score"], lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(queue)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--target-per-class", type=int, default=DEFAULT_TARGET)
    parser.add_argument("--max-per-class", type=int, default=DEFAULT_MAX_PER_CLASS)
    parser.add_argument("--max-per-domain", type=int, default=DEFAULT_MAX_PER_DOMAIN)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)

    try:
        _, rows = _read(args.input)
        queue, summary = suggest_batch(
            rows, target_per_class=args.target_per_class,
            max_per_class=args.max_per_class, max_per_domain=args.max_per_domain,
        )
        write_queue(queue, args.output, overwrite=args.overwrite)
    except SuggestionError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    print(f"Wrote {len(queue)} candidates to {args.output} (IDs and scores only; no labels were assigned).")
    for focus, info in summary.items():
        print(f"- {focus}: verified {info['verified_now']}, requested {info['requested']}, suggested {info['suggested']}")
    print("Next: python -m scripts.label_emails --queue", args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
