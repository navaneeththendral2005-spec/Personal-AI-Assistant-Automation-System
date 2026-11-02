"""Explain legacy label patterns using aggregate-only local analysis.

Sender domains are written only to the private reports output file. Console output
contains aggregate counts only. Features come from FRIDAY's shared extractor.
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.services.email_features import FeatureContext, extract_features, parse_sender

TASK_COLUMNS = {
    "is_spam": "true_is_spam",
    "needs_response": "true_needs_response",
    "priority": "true_priority",
    "category": "true_category",
}
SIGNALS = {
    "noreply_sender": "sender_is_noreply",
    "unsubscribe_text": "body_has_unsubscribe",
    "deadline_words": "hits_deadline",
    "question_marks": "body_question_count",
    "reply_thread": "thread_has_replies",
}


class LegacyPatternError(ValueError):
    """Safe-to-display input or validation error."""


def _read_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise LegacyPatternError(f"Input CSV not found: {path}")
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            headers = reader.fieldnames or []
            required = {"email_id", "sender", "true_needs_response", "true_priority", "true_category"}
            missing = sorted(required - set(headers))
            if missing:
                raise LegacyPatternError(
                    "Input CSV is missing required columns: " + ", ".join(missing)
                )
            rows = []
            for row in reader:
                if None in row:
                    raise LegacyPatternError("Input CSV contains rows with extra cells.")
                rows.append({key: (value or "").strip() for key, value in row.items()})
    except (OSError, csv.Error) as exc:
        raise LegacyPatternError(f"Could not read input CSV ({type(exc).__name__}).") from exc
    return rows


def _normalise(value: str, task: str) -> str:
    value = (value or "").strip()
    if task in {"is_spam", "needs_response"}:
        return value.casefold()
    return "_".join(value.upper().replace("-", " ").split())


def _legacy_label(row: dict[str, str], task: str) -> str:
    value = _normalise(row.get(TASK_COLUMNS[task], ""), task)
    if task == "is_spam" and value not in {"yes", "no"}:
        legacy_priority = _normalise(row.get("true_priority", ""), "priority")
        if legacy_priority == "SPAM":
            return "yes"
        if legacy_priority in {"URGENT", "NEEDS_REPLY", "INFORMATIONAL"}:
            return "no"
    return value


def _safe_domain(sender: str) -> str:
    # Uses the shared parser's same registered-domain heuristic as FRIDAY features.
    return parse_sender(sender).registered_domain


def _feature_signals(row: dict[str, str]) -> dict[str, float]:
    # Extractor has an explicit input allow-list and ignores all true_ columns.
    features = extract_features(row, FeatureContext())
    return {
        "noreply_sender": float(features["sender_is_noreply"] > 0),
        "unsubscribe_text": float(features["body_has_unsubscribe"] > 0),
        "deadline_words": float(features["hits_deadline"] > 0),
        "question_marks": float(features["body_question_count"] > 0),
        "reply_thread": float(features["thread_has_replies"] > 0 or features["is_reply"] > 0),
    }


def _summary_for_task(task: str, distributions: dict[str, Any], features: dict[str, Any]) -> str:
    if task == "is_spam":
        return (
            "Legacy spam labels appear associated with the sender/content signals shown above. "
            "Compare the spam=yes and spam=no rates for bulk/no-reply senders and unsubscribe text; "
            "these are descriptive correlations, not proof of the original decision rule."
        )
    if task == "needs_response":
        return (
            "Legacy needs_response labels can be interpreted by comparing request-like message signals "
            "and reply-thread/question-mark rates between yes and no. These signals are descriptive only."
        )
    if task == "priority":
        return (
            "Legacy URGENT labels can be interpreted by comparing deadline-word rates and stated urgency "
            "signals against INFORMATIONAL and NEEDS_REPLY. Keyword presence alone does not establish a real deadline."
        )
    return (
        "Legacy category meaning is summarized by class prevalence and the sender-domain patterns. "
        "Domain concentration may reflect the email sources present in this dataset, not a universal category rule."
    )


def analyze_legacy_patterns(rows: list[dict[str, str]], *, top_domains: int = 10) -> dict[str, Any]:
    if top_domains < 1:
        raise LegacyPatternError("top_domains must be at least 1.")
    seen_ids: set[str] = set()
    for row in rows:
        email_id = (row.get("email_id") or "").strip()
        if not email_id or email_id in seen_ids:
            raise LegacyPatternError("Input identity validation failed (missing or duplicate IDs).")
        seen_ids.add(email_id)

    domain_by_row = [_safe_domain(row.get("sender", "")) for row in rows]
    features_by_row = [_feature_signals(row) for row in rows]
    task_results: dict[str, Any] = {}

    for task in TASK_COLUMNS:
        valid_rows: list[tuple[int, str]] = []
        for index, row in enumerate(rows):
            label = _legacy_label(row, task)
            if label:
                valid_rows.append((index, label))
        counts = Counter(label for _, label in valid_rows)
        total = len(valid_rows)
        classes: dict[str, Any] = {}
        domain_counts: dict[str, Counter[str]] = defaultdict(Counter)
        domain_label_counts: dict[str, Counter[str]] = defaultdict(Counter)
        signal_rows: dict[str, list[dict[str, float]]] = defaultdict(list)

        for index, label in valid_rows:
            domain = domain_by_row[index]
            if domain:
                domain_counts[label][domain] += 1
                domain_label_counts[domain][label] += 1
            signal_rows[label].append(features_by_row[index])

        for label, count in sorted(counts.items()):
            class_signal_rows = signal_rows[label]
            signal_summary = {}
            for signal in SIGNALS:
                values = [entry[signal] for entry in class_signal_rows]
                signal_summary[signal] = {
                    "count_present": int(sum(value > 0 for value in values)),
                    "rate_present": round(sum(value > 0 for value in values) / len(values), 4) if values else None,
                    "mean_value": round(sum(values) / len(values), 4) if values else None,
                }
            classes[label] = {
                "count": count,
                "share": round(count / total, 4) if total else 0.0,
                "feature_signals": signal_summary,
            }

        top_by_class = {
            label: [
                {"registered_domain": domain, "count": count}
                for domain, count in domain_counts[label].most_common(top_domains)
            ]
            for label in sorted(counts)
        }
        inconsistent_domains = []
        for domain, labels_for_domain in sorted(domain_label_counts.items()):
            if len(labels_for_domain) > 1:
                inconsistent_domains.append({
                    "registered_domain": domain,
                    "total_messages": sum(labels_for_domain.values()),
                    "labels": dict(sorted(labels_for_domain.items())),
                    "distinct_labels": len(labels_for_domain),
                })
        inconsistent_domains.sort(key=lambda item: (-item["total_messages"], item["registered_domain"]))

        task_results[task] = {
            "labeled_count": total,
            "unlabeled_count": len(rows) - total,
            "classes": classes,
            "top_registered_domains_by_class": top_by_class,
            "domain_inconsistency": {
                "domains_with_multiple_labels": len(inconsistent_domains),
                "domains": inconsistent_domains,
            },
            "interpretation": _summary_for_task(task, classes, {}),
        }

    return {
        "input_row_count": len(rows),
        "tasks": task_results,
        "feature_signal_definitions": {
            "noreply_sender": "Shared extractor sender_is_noreply > 0",
            "unsubscribe_text": "Shared extractor body_has_unsubscribe > 0",
            "deadline_words": "Shared extractor hits_deadline > 0",
            "question_marks": "Shared extractor body_question_count > 0",
            "reply_thread": "Shared extractor thread_has_replies > 0 or is_reply > 0",
        },
        "privacy_note": "Aggregate report only. Sender domains appear only in this private reports file; no message content or email IDs are included.",
        "method_note": (
            "Registered domains use backend.services.email_features.parse_sender. "
            "Feature signals use backend.services.email_features.extract_features in independent/default context. "
            "Domain label variation is descriptive evidence of within-domain label inconsistency, not proof of error."
        ),
    }


def _backup_existing(path: Path, backup_dir: Path) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = backup_dir / f"{path.stem}.{stamp}{path.suffix}"
    shutil.copy2(path, backup)
    return backup


def write_report(report: dict[str, Any], output: Path, *, overwrite: bool = False,
                 backup_dir: Path = Path("data/backups")) -> None:
    if output.exists() and not overwrite:
        raise LegacyPatternError("Report already exists. Use --overwrite to replace it; a backup will be created.")
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        _backup_existing(output, backup_dir)
    temp = output.with_name(output.name + ".tmp")
    try:
        temp.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        temp.replace(output)
    finally:
        if temp.exists():
            temp.unlink()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("data/email_labels.csv"))
    parser.add_argument("--output", type=Path, default=Path("reports/legacy_label_patterns.json"))
    parser.add_argument("--top-domains", type=int, default=10)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    try:
        rows = _read_rows(args.input)
        report = analyze_legacy_patterns(rows, top_domains=args.top_domains)
        write_report(report, args.output, overwrite=args.overwrite)
    except LegacyPatternError as exc:
        print(f"Legacy pattern analysis stopped: {exc}", file=sys.stderr)
        return 2
    print("Legacy label pattern analysis complete (aggregate counts only):")
    print(f"input rows: {report['input_row_count']}")
    for task, task_report in report["tasks"].items():
        class_count = len(task_report["classes"])
        inconsistent = task_report["domain_inconsistency"]["domains_with_multiple_labels"]
        print(f"{task}: labeled={task_report['labeled_count']}, classes={class_count}, inconsistent_domains={inconsistent}")
    print(f"Private aggregate report: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
