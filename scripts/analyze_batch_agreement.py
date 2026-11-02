from __future__ import annotations

import argparse
import csv
import json
import shutil
from collections import Counter
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from typing import Any

LABELS = {
    "is_spam": ("no", "yes"),
    "needs_response": ("no", "yes"),
    "priority": ("INFORMATIONAL", "NEEDS_REPLY", "URGENT"),
    "category": ("FINANCE", "JOB", "OTHER", "PERSONAL", "PROMOTIONS", "UPDATES"),
}
REQUIRED = {
    "email_id", "batch_name", "guide_version", "is_spam",
    "needs_response", "priority", "category", "unsure",
}


def normalize(value: str | None, task: str) -> str:
    value = (value or "").strip()
    if task in {"is_spam", "needs_response", "unsure"}:
        return value.casefold()
    value = "_".join(value.upper().replace("-", " ").split())
    if task == "priority" and value == "NEEDSREPLY":
        return "NEEDS_REPLY"
    if task == "category" and value == "UPDATE":
        return "UPDATES"
    return value


def cohen_kappa(
    first: list[str],
    second: list[str],
) -> float | None:
    """Calculate unweighted Cohen's kappa.

    Returns:
        1.0: Perfect agreement.
        None: Kappa is mathematically undefined.
        float: Calculated kappa otherwise.
    """
    if len(first) != len(second):
        raise ValueError(
            "Both annotation lists must have equal length."
        )

    if not first:
        return None

    total = len(first)

    # Observed agreement
    observed_agreement = sum(
        a == b for a, b in zip(first, second)
    ) / total

    # Expected agreement by chance
    labels = set(first) | set(second)

    expected_agreement = sum(
        (first.count(label) / total)
        * (second.count(label) / total)
        for label in labels
    )

    denominator = 1.0 - expected_agreement

    # Kappa is undefined when expected agreement is 100%.
    if abs(denominator) < 1e-12:
        return None

    kappa = (
        observed_agreement - expected_agreement
    ) / denominator

    return kappa


def read_annotations(path: Path) -> tuple[list[dict[str, str]], dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        missing = REQUIRED - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"Ground-truth CSV is missing {len(missing)} required columns")
        rows = list(reader)

    seen: set[tuple[str, str]] = set()
    guide_by_batch: dict[str, set[str]] = {}
    for row in rows:
        email_id = (row.get("email_id") or "").strip()
        batch = (row.get("batch_name") or "").strip()
        guide = (row.get("guide_version") or "").strip()
        if not email_id or not batch or not guide:
            raise ValueError("Each annotation requires email_id, batch_name, and guide_version")
        key = (email_id, batch)
        if key in seen:
            raise ValueError("Duplicate email_id/batch_name annotation found")
        seen.add(key)
        guide_by_batch.setdefault(batch, set()).add(guide)
    if any(len(versions) != 1 for versions in guide_by_batch.values()):
        raise ValueError("A batch contains multiple guide versions; resolve provenance before scoring")
    guides = {batch: next(iter(versions)) for batch, versions in guide_by_batch.items()}
    return rows, guides


def _label(row: dict[str, str], task: str) -> str:
    value = normalize(row.get(task), task)
    if value and value not in LABELS[task]:
        raise ValueError(f"Invalid {task} label found in ground-truth CSV")
    return value


def _matrix(task: str, first: list[str], second: list[str]) -> dict[str, dict[str, int]]:
    labels = LABELS[task]
    result = {a: {b: 0 for b in labels} for a in labels}
    for a, b in zip(first, second):
        result[a][b] += 1
    return result


def _summarize(first: list[str], second: list[str]) -> dict[str, Any]:
    n = len(first)
    matches = sum(a == b for a, b in zip(first, second))
    kappa = cohen_kappa(first, second)
    return {
        "paired_annotations": n,
        "exact_agreement_count": matches,
        "raw_agreement": round(matches / n, 4) if n else None,
        "cohen_kappa": round(kappa, 4) if kappa is not None else None,
        "kappa_status": "computed" if kappa is not None else "undefined_or_no_pairs",
    }


def score_pair(
    left: dict[str, dict[str, str]],
    right: dict[str, dict[str, str]],
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    shared_ids = set(left) & set(right)
    results: dict[str, Any] = {}
    matrix_rows: list[dict[str, str]] = []

    for task in LABELS:
        all_a: list[str] = []
        all_b: list[str] = []
        confident_a: list[str] = []
        confident_b: list[str] = []
        unsure_excluded = 0
        unavailable_excluded = 0
        priority_na_excluded = 0

        for email_id in shared_ids:
            a, b = left[email_id], right[email_id]
            if task == "priority" and (
                normalize(a.get("is_spam"), "is_spam") == "yes"
                or normalize(b.get("is_spam"), "is_spam") == "yes"
            ):
                priority_na_excluded += 1
                continue
            label_a, label_b = _label(a, task), _label(b, task)
            if not label_a or not label_b:
                unavailable_excluded += 1
                continue
            all_a.append(label_a)
            all_b.append(label_b)
            if normalize(a.get("unsure"), "unsure") == "yes" or normalize(b.get("unsure"), "unsure") == "yes":
                unsure_excluded += 1
            else:
                confident_a.append(label_a)
                confident_b.append(label_b)

        matrix_all = _matrix(task, all_a, all_b)
        matrix_confident = _matrix(task, confident_a, confident_b)
        results[task] = {
            "all_valid_annotations": _summarize(all_a, all_b),
            "excluding_unsure_annotations": _summarize(confident_a, confident_b),
            "excluded_unsure_pairs": unsure_excluded,
            "excluded_unavailable_pairs": unavailable_excluded,
            "excluded_priority_not_applicable_pairs": priority_na_excluded,
            "confusion_matrix_all_valid": matrix_all,
            "confusion_matrix_confident_only": matrix_confident,
        }
        for scope, matrix in (("all_valid", matrix_all), ("confident_only", matrix_confident)):
            for label_a in LABELS[task]:
                for label_b in LABELS[task]:
                    matrix_rows.append({
                        "scope": scope, "task": task,
                        "first_label": label_a, "second_label": label_b,
                        "count": str(matrix[label_a][label_b]),
                    })
    return {
        "shared_email_count": len(shared_ids),
        "tasks": results,
    }, matrix_rows


def analyze(path: Path) -> tuple[dict[str, Any], list[dict[str, str]]]:
    rows, guides = read_annotations(path)
    by_batch: dict[str, dict[str, dict[str, str]]] = {}
    for row in rows:
        by_batch.setdefault(row["batch_name"].strip(), {})[row["email_id"].strip()] = row

    pairs = []
    all_matrix_rows = []
    for batch_a, batch_b in combinations(sorted(by_batch), 2):
        pair, matrix_rows = score_pair(by_batch[batch_a], by_batch[batch_b])
        if not pair["shared_email_count"]:
            continue
        comparison = "within_guide_version" if guides[batch_a] == guides[batch_b] else "cross_guide_version"
        pairs.append({
            "batch_a": batch_a, "guide_a": guides[batch_a],
            "batch_b": batch_b, "guide_b": guides[batch_b],
            "comparison_type": comparison,
            **pair,
        })
        for row in matrix_rows:
            all_matrix_rows.append({
                "batch_a": batch_a, "guide_a": guides[batch_a],
                "batch_b": batch_b, "guide_b": guides[batch_b], **row,
            })

    report = {
        "report_version": "1.0",
        "method": "Pairwise unweighted Cohen's kappa, raw agreement, and confusion matrices",
        "annotation_rows": len(rows),
        "unique_emails": len({row["email_id"].strip() for row in rows}),
        "batches": [
            {"batch_name": name, "guide_version": guides[name], "annotation_count": len(by_batch[name])}
            for name in sorted(by_batch)
        ],
        "batch_pairs_with_overlap": len(pairs),
        "pairs": pairs,
        "interpretation_note": (
            "Cross-version comparisons are not same-guide self-consistency. Small overlap counts "
            "are descriptive and should not be overinterpreted. Confident-only results exclude "
            "a pair if either annotation has unsure=yes. Priority comparisons are excluded if "
            "either annotation marks the email as spam."
        ),
        "privacy_note": "Aggregate-only output; no email IDs or message content.",
    }
    return report, all_matrix_rows


def _backup(paths: list[Path], backup_dir: Path) -> None:
    existing = [path for path in paths if path.exists()]
    if not existing:
        return
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for path in existing:
        target = backup_dir / f"{path.stem}_{stamp}{path.suffix}"
        if target.exists():
            raise FileExistsError("Timestamped backup already exists; retry shortly")
        shutil.copy2(path, target)


def write_reports(
    report: dict[str, Any],
    matrix_rows: list[dict[str, str]],
    output: Path,
    confusion_csv: Path,
    overwrite: bool = False,
    backup_dir: Path = Path("data/backups"),
) -> None:
    paths = [output, confusion_csv]
    if output.resolve() == confusion_csv.resolve():
        raise ValueError("JSON and CSV report paths must be different")
    if any(path.exists() for path in paths):
        if not overwrite:
            raise FileExistsError("Report output already exists; use --overwrite to back up and replace")
        _backup(paths, backup_dir)

    output.parent.mkdir(parents=True, exist_ok=True)
    confusion_csv.parent.mkdir(parents=True, exist_ok=True)
    json_tmp = output.with_suffix(output.suffix + ".tmp")
    csv_tmp = confusion_csv.with_suffix(confusion_csv.suffix + ".tmp")
    fields = ["batch_a", "guide_a", "batch_b", "guide_b", "scope", "task",
              "first_label", "second_label", "count"]
    try:
        json_tmp.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        with csv_tmp.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(matrix_rows)
        json_tmp.replace(output)
        csv_tmp.replace(confusion_csv)
    finally:
        for temp in (json_tmp, csv_tmp):
            if temp.exists():
                temp.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("data/email_labels_v2.csv"))
    parser.add_argument("--output", type=Path, default=Path("reports/batch_agreement_v2.json"))
    parser.add_argument("--confusion-csv", type=Path, default=Path("reports/batch_confusion_v2.csv"))
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--backup-dir", type=Path, default=Path("data/backups"))
    args = parser.parse_args()
    try:
        report, matrices = analyze(args.input)
        write_reports(report, matrices, args.output, args.confusion_csv, args.overwrite, args.backup_dir)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print("Batch agreement analysis complete (aggregate counts only):")
    print(f"annotation_rows: {report['annotation_rows']}")
    print(f"unique_emails: {report['unique_emails']}")
    print(f"batch_pairs_with_overlap: {report['batch_pairs_with_overlap']}")
    for pair in report["pairs"]:
        print(f"{pair['batch_a']} vs {pair['batch_b']}: shared={pair['shared_email_count']} ({pair['comparison_type']})")
    print(f"JSON report: {args.output}")
    print(f"Confusion matrices: {args.confusion_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
