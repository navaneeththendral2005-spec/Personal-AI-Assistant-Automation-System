"""Aggregate-only comparison of legacy human labels and v2 annotations.

AI draft rows are refused by default. Use --allow-draft-comparison only for
diagnostic comparisons; such reports are explicitly NOT_A_SELF_CONSISTENCY_MEASURE.

When the v2 file has a ``labeler`` column (written by scripts.import_labeling_workbook
and scripts.quarantine_ai_drafts), only ``labeler=human`` rows are ever compared and
the report says how many other rows were excluded. ``--guide-version`` and
``--batch-name`` select one annotation round, because the importer deliberately keeps
several annotations of the same email (different guide versions or batches).
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Sequence

TASKS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "is_spam": ("true_is_spam", "is_spam", ("no", "yes")),
    "needs_response": ("true_needs_response", "needs_response", ("no", "yes")),
    "priority": ("true_priority", "priority", ("URGENT", "NEEDS_REPLY", "INFORMATIONAL")),
    "category": ("true_category", "category", ("JOB", "FINANCE", "PERSONAL", "PROMOTIONS", "UPDATES", "OTHER")),
}
DRAFT_PREFIX = "claude_draft:"
# v2.1 merges the legacy PERSONAL class into OTHER (docs/EMAIL_LABELING_GUIDE.md section 6).
CATEGORY_V21 = ("JOB", "FINANCE", "PROMOTIONS", "UPDATES", "OTHER")


class AgreementError(ValueError):
    """A safe-to-display input or validation error."""


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    if not path.is_file():
        raise AgreementError(f"Input file not found: {path}")
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            headers = list(reader.fieldnames or [])
            rows = [
                {key: (value or "").strip() for key, value in row.items() if key is not None}
                for row in reader
            ]
    except (OSError, csv.Error) as exc:
        raise AgreementError(f"Could not read {path.name} ({type(exc).__name__}).") from exc
    if not headers:
        raise AgreementError(f"{path.name} has no header row.")
    return headers, rows


def _norm(value: str | None, task: str) -> str:
    value = (value or "").strip()
    if task in {"is_spam", "needs_response"}:
        return value.casefold()
    normalized = "_".join(value.upper().replace("-", " ").split())
    if task == "priority" and normalized in {"NEEDSREPLY", "NEEDS_REPLY"}:
        return "NEEDS_REPLY"
    if task == "category" and normalized == "UPDATE":
        return "UPDATES"
    return normalized


def _has_ai_draft(rows: list[dict[str, str]]) -> bool:
    return any((row.get("note") or "").strip().casefold().startswith(DRAFT_PREFIX) for row in rows)


def _legacy_value(row: dict[str, str], task: str) -> str:
    old_column = TASKS[task][0]
    value = _norm(row.get(old_column), task)
    if task == "is_spam" and value not in {"yes", "no"}:
        # Historical schema stored SPAM in true_priority for some records.
        legacy_priority = _norm(row.get("true_priority"), "priority")
        if legacy_priority == "SPAM":
            return "yes"
        if legacy_priority in TASKS["priority"][2]:
            return "no"
    return value


def cohen_kappa(first: list[str], second: list[str]) -> float | None:
    if len(first) != len(second):
        raise ValueError("Annotation lists must have equal length.")
    if not first:
        return None
    labels = set(first) | set(second)
    n = len(first)
    observed = sum(a == b for a, b in zip(first, second)) / n
    expected = sum(
        (sum(value == label for value in first) / n)
        * (sum(value == label for value in second) / n)
        for label in labels
    )
    if expected == 1.0:
        return None
    return (observed - expected) / (1.0 - expected)


def _class_prevalence(values: list[str], labels: tuple[str, ...]) -> dict[str, float]:
    n = len(values)
    return {label: round(values.count(label) / n, 4) if n else 0.0 for label in labels}


def _per_class_agreement(pairs: list[tuple[str, str]], labels: tuple[str, ...]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    n = len(pairs)
    for label in labels:
        both = sum(a == label and b == label for a, b in pairs)
        first_count = sum(a == label for a, _ in pairs)
        second_count = sum(b == label for _, b in pairs)
        denominator = first_count + second_count
        result[label] = {
            "both_raters_label_count": both,
            "legacy_count": first_count,
            "v2_count": second_count,
            "class_specific_agreement": round((2 * both / denominator), 4) if denominator else None,
            "share_of_paired_rows": round(both / n, 4) if n else None,
        }
    return result


def _pabak_interval(pairs: list[tuple[str, str]], *, iterations: int, seed: int) -> list[float] | None:
    if len(pairs) < 20:
        return None
    rng = random.Random(seed)
    n = len(pairs)
    estimates = []
    for _ in range(iterations):
        sample = [pairs[rng.randrange(n)] for _ in range(n)]
        agreement = sum(a == b for a, b in sample) / n
        estimates.append(2.0 * agreement - 1.0)
    estimates.sort()
    low_index = max(0, int(0.025 * iterations))
    high_index = min(iterations - 1, int(0.975 * iterations))
    return [round(estimates[low_index], 4), round(estimates[high_index], 4)]


def _task_metrics(
    pairs: list[tuple[str, str]],
    labels: tuple[str, ...],
    *,
    task: str,
    bootstrap_iterations: int,
    seed: int,
) -> dict[str, Any]:
    legacy = [a for a, _ in pairs]
    v2 = [b for _, b in pairs]
    n = len(pairs)
    agreement_count = sum(a == b for a, b in pairs)
    raw = agreement_count / n if n else None
    legacy_classes = len(set(legacy))
    v2_classes = len(set(v2))
    degenerate = bool(n and (legacy_classes <= 1 or v2_classes <= 1))
    kappa = cohen_kappa(legacy, v2)
    result: dict[str, Any] = {
        "paired_annotations": n,
        "raw_agreement": round(raw, 4) if raw is not None else None,
        "agreement_count": agreement_count,
        "kappa": "undefined_degenerate" if degenerate else (round(kappa, 4) if kappa is not None else None),
        "kappa_message": (
            "Kappa is undefined for interpretation because at least one rater used only one class."
            if degenerate else
            "Kappa is undefined because expected agreement equals 1."
            if kappa is None else
            "Cohen's kappa computed from paired valid annotations."
        ),
        "prevalence": {
            "legacy": _class_prevalence(legacy, labels),
            "v2": _class_prevalence(v2, labels),
        },
        "per_class_agreement": _per_class_agreement(pairs, labels),
        "class_counts_legacy": dict(sorted(Counter(legacy).items())),
        "class_counts_v2": dict(sorted(Counter(v2).items())),
        # Rows = legacy label, columns = v2 label.
        "confusion_matrix": {
            first: {second: sum(a == first and b == second for a, b in pairs) for second in labels}
            for first in labels
        },
    }
    if len(labels) == 2:
        pabak = (2 * raw - 1) if raw is not None else None
        result["pabak"] = round(pabak, 4) if pabak is not None else None
        interval = _pabak_interval(
            pairs, iterations=bootstrap_iterations, seed=seed + sum(ord(c) for c in task)
        )
        result["pabak_bootstrap_95_interval"] = interval
        result["pabak_interval_method"] = (
            f"Percentile bootstrap, {bootstrap_iterations} resamples, fixed task-specific seed."
            if interval is not None else "Not calculated: fewer than 20 paired annotations."
        )
    return result


def _provenance_selection(
    v2_rows: list[dict[str, str]],
    *,
    guide_version: str | None,
    batch_names: Sequence[str] | None,
    require_human_provenance: bool,
    label_round: str | None = None,
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    """Keep only human rows (when provenance is recorded) and the requested round."""
    labeler_present = bool(v2_rows) and "labeler" in v2_rows[0]
    if require_human_provenance and not labeler_present:
        raise AgreementError(
            "The v2 file has no 'labeler' column, so who produced each label is unknown. "
            "Run scripts.quarantine_ai_drafts to declare human vs AI-draft batches first."
        )
    selection: dict[str, Any] = {
        "labeler_column_present": labeler_present,
        "rows_in_file": len(v2_rows),
        "rows_by_labeler": dict(sorted(Counter((r.get("labeler") or "(blank)").casefold() for r in v2_rows).items()))
        if labeler_present else {},
    }
    selected = list(v2_rows)
    if labeler_present:
        selected = [r for r in selected if (r.get("labeler") or "").strip().casefold() == "human"]
    selection["excluded_non_human_rows"] = len(v2_rows) - len(selected) if labeler_present else 0

    before_filter = len(selected)
    if guide_version is not None:
        if v2_rows and "guide_version" not in v2_rows[0]:
            raise AgreementError("--guide-version needs a 'guide_version' column in the v2 file.")
        selected = [r for r in selected if (r.get("guide_version") or "").strip() == guide_version]
    if batch_names:
        if v2_rows and "batch_name" not in v2_rows[0]:
            raise AgreementError("--batch-name needs a 'batch_name' column in the v2 file.")
        wanted = {name.strip() for name in batch_names}
        selected = [r for r in selected if (r.get("batch_name") or "").strip() in wanted]
    before_round_filter = len(selected)
    if label_round is not None:
        if v2_rows and "label_round" not in v2_rows[0]:
            raise AgreementError("--label-round needs a 'label_round' column in the v2 file.")
        selected = [r for r in selected if (r.get("label_round") or "").strip().casefold() == label_round.casefold()]
    selection["label_round_filter"] = label_round
    selection["excluded_by_label_round"] = before_round_filter - len(selected)
    selection["excluded_by_filter"] = before_filter - len(selected)
    selection["guide_versions_in_selection"] = sorted({(r.get("guide_version") or "").strip() for r in selected} - {""})
    selection["batches_in_selection"] = sorted({(r.get("batch_name") or "").strip() for r in selected} - {""})
    return selected, selection


def compare_label_rows(
    legacy_rows: list[dict[str, str]],
    v2_rows: list[dict[str, str]],
    *,
    allow_draft_comparison: bool = False,
    bootstrap_iterations: int = 2000,
    seed: int = 20261101,
    guide_version: str | None = None,
    batch_names: Sequence[str] | None = None,
    require_human_provenance: bool = False,
    merge_personal_into_other: bool = False,
    label_round: str | None = None,
) -> dict[str, Any]:
    """Compare already-loaded rows. See the module docstring for the provenance rules."""
    if bootstrap_iterations < 100:
        raise AgreementError("bootstrap_iterations must be at least 100.")
    selected_v2, selection = _provenance_selection(
        v2_rows, guide_version=guide_version, batch_names=batch_names,
        require_human_provenance=require_human_provenance, label_round=label_round,
    )
    if v2_rows and not selected_v2:
        raise AgreementError("No v2 annotations remain after the human-provenance and round selection.")
    draft_found = _has_ai_draft(legacy_rows) or _has_ai_draft(selected_v2)
    if draft_found and not allow_draft_comparison:
        raise AgreementError(
            "AI-draft rows were detected. Refusing comparison; use --allow-draft-comparison "
            "only for diagnostics, not self-consistency measurement."
        )

    def index_rows(rows: list[dict[str, str]], label: str) -> dict[str, dict[str, str]]:
        indexed: dict[str, dict[str, str]] = {}
        for row in rows:
            key = (row.get("email_id") or "").strip()
            if not key:
                raise AgreementError(f"{label} file contains a blank email_id.")
            if key in indexed:
                hint = (
                    " Select one annotation round with --guide-version and/or --batch-name."
                    if label == "v2" else ""
                )
                raise AgreementError(f"{label} file contains duplicate email_id values.{hint}")
            indexed[key] = row
        return indexed

    legacy = index_rows(legacy_rows, "legacy")
    v2 = index_rows(selected_v2, "v2")
    common = sorted(set(legacy) & set(v2))
    if not common:
        raise AgreementError("No matching email IDs were found between the input datasets.")

    def category_value(value: str) -> str:
        return "OTHER" if merge_personal_into_other and value == "PERSONAL" else value

    task_results: dict[str, Any] = {}
    for task, (_, new_column, labels) in TASKS.items():
        if task == "category" and merge_personal_into_other:
            labels = CATEGORY_V21
        pairs: list[tuple[str, str]] = []
        unavailable = 0
        excluded_spam = 0
        for key in common:
            old_value = _legacy_value(legacy[key], task)
            new_value = _norm(v2[key].get(new_column), task)
            if task == "category":
                old_value, new_value = category_value(old_value), category_value(new_value)
            if task == "priority":
                if _legacy_value(legacy[key], "is_spam") == "yes" or _norm(v2[key].get("is_spam"), "is_spam") == "yes":
                    excluded_spam += 1
                    continue
            if old_value not in labels or new_value not in labels:
                unavailable += 1
                continue
            pairs.append((old_value, new_value))
        metrics = _task_metrics(
            pairs, labels, task=task,
            bootstrap_iterations=bootstrap_iterations, seed=seed,
        )
        metrics["unavailable_or_invalid_pairs"] = unavailable
        if task == "priority":
            metrics["excluded_spam_not_applicable"] = excluded_spam
        if task == "category" and merge_personal_into_other:
            metrics["category_view"] = "v2.1: legacy PERSONAL merged into OTHER for comparison only; stored labels are unchanged"
        task_results[task] = metrics

    all_human = (not selection["labeler_column_present"]) or all(
        (r.get("labeler") or "").strip().casefold() == "human" for r in selected_v2
    )
    return {
        "report_status": "NOT_A_SELF_CONSISTENCY_MEASURE" if draft_found else "HUMAN_ANNOTATION_COMPARISON",
        "ai_draft_rows_detected": draft_found,
        "provenance_verified": bool(selection["labeler_column_present"] and all_human and not draft_found),
        "v2_selection": selection,
        "matched_email_count": len(common),
        "legacy_only_count": len(set(legacy) - set(v2)),
        "v2_only_count": len(set(v2) - set(legacy)),
        "tasks": task_results,
        "privacy_note": "Aggregate statistics only; no email IDs, senders, subjects, or message text.",
        "interpretation_note": "Agreement statistics compare annotations; they do not establish which annotation is correct.",
    }


def compare_labels(
    legacy_path: Path,
    v2_path: Path,
    *,
    allow_draft_comparison: bool = False,
    bootstrap_iterations: int = 2000,
    seed: int = 20261101,
    guide_version: str | None = None,
    batch_names: Sequence[str] | None = None,
    require_human_provenance: bool = False,
    merge_personal_into_other: bool = False,
    label_round: str | None = None,
) -> dict[str, Any]:
    _, legacy_rows = _read_csv(legacy_path)
    _, v2_rows = _read_csv(v2_path)
    return compare_label_rows(
        legacy_rows, v2_rows,
        allow_draft_comparison=allow_draft_comparison,
        bootstrap_iterations=bootstrap_iterations, seed=seed,
        guide_version=guide_version, batch_names=batch_names,
        require_human_provenance=require_human_provenance,
        merge_personal_into_other=merge_personal_into_other, label_round=label_round,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--legacy", type=Path, default=Path("data/email_labels.csv"))
    parser.add_argument("--v2", type=Path, default=Path("data/email_labels_v2.csv"))
    parser.add_argument("--output", type=Path, default=Path("reports/label_agreement_v2.json"))
    parser.add_argument("--allow-draft-comparison", action="store_true")
    parser.add_argument("--bootstrap-iterations", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=20261101)
    parser.add_argument("--guide-version", default=None, help="Only compare annotations made under this guide version.")
    parser.add_argument("--batch-name", action="append", default=None, help="Only compare these batches (repeatable).")
    parser.add_argument("--label-round", default=None, help="Only compare this annotation round, e.g. blind_relabel.")
    parser.add_argument("--require-human-provenance", action="store_true",
                        help="Refuse unless the v2 file records who labeled each row.")
    parser.add_argument("--merge-personal-into-other", action="store_true",
                        help="v2.1 category view: treat legacy PERSONAL as OTHER (comparison only).")
    args = parser.parse_args(argv)
    try:
        report = compare_labels(
            args.legacy, args.v2,
            allow_draft_comparison=args.allow_draft_comparison,
            bootstrap_iterations=args.bootstrap_iterations,
            seed=args.seed,
            guide_version=args.guide_version, batch_names=args.batch_name,
            require_human_provenance=args.require_human_provenance,
            merge_personal_into_other=args.merge_personal_into_other, label_round=args.label_round,
        )
    except AgreementError as exc:
        print(f"Agreement analysis stopped: {exc}", file=sys.stderr)
        return 2

    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        print(f"Agreement analysis stopped: output already exists: {args.output}", file=sys.stderr)
        return 2
    temp = args.output.with_name(args.output.name + ".tmp")
    try:
        temp.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        temp.replace(args.output)
    finally:
        if temp.exists():
            temp.unlink()
    print(f"Report status: {report['report_status']}")
    print(f"Provenance verified: {report['provenance_verified']}")
    print(f"Matched annotations: {report['matched_email_count']}")

    for task, metrics in report["tasks"].items():
        print(
            f"{task}: n={metrics['paired_annotations']}, "
            f"agreement={metrics['raw_agreement']}, kappa={metrics['kappa']}"
        )
    print(f"Aggregate-only report: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
