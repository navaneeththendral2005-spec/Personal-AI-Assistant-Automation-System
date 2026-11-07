"""Pre-registered agreement gate for the human v2.1 labeling pilot.

Run from the project root after YOU have labeled the blind pilot workbook::

    python -m scripts.import_labeling_workbook v21_pilot.xlsx --labeler human
    python -m scripts.agreement_gate --guide-version v2.1

The rule (fixed in advance, not tuned after seeing results)
-----------------------------------------------------------
* Gated tasks: ``is_spam`` and ``needs_response``. Each needs Cohen's kappa >= 0.6
  between your legacy labels and your new blind human labels.
* ``priority`` and ``category`` are reported for information and do not gate.
* Only ``labeler=human`` rows under the chosen guide version count. AI drafts and
  rows of unknown provenance are never used.
* A task is INCONCLUSIVE, not passed or failed, when there are too few paired rows
  (default < 20) or when either rater used a single class (kappa is undefined).
* Overall: any FAIL -> FAIL; every gated task PASS -> PASS; otherwise INCONCLUSIVE.
* A FAIL means "rewrite the confusable rules", never "label more": the report names the
  label pairs you mixed up and the guide sections to revisit.

Exit codes: 0 PASS, 1 FAIL, 3 INCONCLUSIVE, 2 error. The report is aggregate-only.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from scripts.compare_label_agreement_v2 import AgreementError, _read_csv, compare_label_rows
from scripts.import_labeling_workbook import _backup

GATED_TASKS = ("is_spam", "needs_response")
DEFAULT_THRESHOLD = 0.6
DEFAULT_MIN_PAIRS = 20
GUIDE_SECTIONS = {
    "is_spam": "docs/EMAIL_LABELING_GUIDE.md sections 3.1 and 5.1 (spam vs PROMOTIONS)",
    "needs_response": "docs/EMAIL_LABELING_GUIDE.md sections 3.2 and 5.2 (needs response vs priority)",
    "priority": "docs/EMAIL_LABELING_GUIDE.md sections 3.3 and 5.7",
    "category": "docs/EMAIL_LABELING_GUIDE.md sections 3.4, 5.3, 5.5 and 5.6",
}
EXIT_CODES = {"PASS": 0, "FAIL": 1, "INCONCLUSIVE": 3}


class GateError(ValueError):
    """A safe-to-display input or provenance problem."""


def _top_confusions(matrix: dict[str, dict[str, int]], limit: int = 3) -> list[dict[str, Any]]:
    pairs = [
        {"legacy_label": a, "v2_label": b, "count": count}
        for a, row in matrix.items() for b, count in row.items()
        if a != b and count > 0
    ]
    pairs.sort(key=lambda item: (-item["count"], item["legacy_label"], item["v2_label"]))
    return pairs[:limit]


def _task_verdict(metrics: dict[str, Any], *, threshold: float, min_pairs: int) -> tuple[str, str]:
    n = metrics["paired_annotations"]
    kappa = metrics["kappa"]
    if n < min_pairs:
        return "INCONCLUSIVE", f"Only {n} paired annotations; at least {min_pairs} are required."
    if not isinstance(kappa, (int, float)):
        return "INCONCLUSIVE", (
            "Kappa is undefined because at least one rater used a single class. Read the raw "
            "agreement and prevalence instead and check whether the definitions allow both classes."
        )
    if kappa >= threshold:
        return "PASS", f"kappa {kappa} >= {threshold}."
    return "FAIL", f"kappa {kappa} < {threshold}."


def evaluate_gate(
    legacy_rows: list[dict[str, str]],
    v2_rows: list[dict[str, str]],
    *,
    guide_version: str = "v2.1",
    batch_names: Sequence[str] | None = None,
    threshold: float = DEFAULT_THRESHOLD,
    min_pairs: int = DEFAULT_MIN_PAIRS,
    bootstrap_iterations: int = 2000,
    seed: int = 20261101,
) -> dict[str, Any]:
    if not 0.0 < threshold <= 1.0:
        raise GateError("threshold must be in (0, 1].")
    if min_pairs < 2:
        raise GateError("min_pairs must be at least 2.")
    merge = guide_version.startswith("v2.1")
    common = dict(
        guide_version=guide_version, batch_names=batch_names, require_human_provenance=True,
        merge_personal_into_other=merge, label_round="blind_relabel",
        bootstrap_iterations=bootstrap_iterations, seed=seed,
    )
    try:
        primary = compare_label_rows(legacy_rows, v2_rows, **common)
    except AgreementError as exc:
        raise GateError(str(exc)) from exc

    # Secondary view: the same comparison without rows you marked unsure=yes.
    confident_v2 = [r for r in v2_rows if (r.get("unsure") or "").strip().casefold() != "yes"]
    secondary: dict[str, Any] | None
    try:
        secondary = compare_label_rows(legacy_rows, confident_v2, **common)
    except AgreementError:
        secondary = None

    verdicts: dict[str, Any] = {}
    for task in GATED_TASKS:
        status, reason = _task_verdict(primary["tasks"][task], threshold=threshold, min_pairs=min_pairs)
        entry: dict[str, Any] = {
            "status": status,
            "reason": reason,
            "paired_annotations": primary["tasks"][task]["paired_annotations"],
            "kappa": primary["tasks"][task]["kappa"],
            "raw_agreement": primary["tasks"][task]["raw_agreement"],
            "kappa_excluding_unsure": (
                secondary["tasks"][task]["kappa"] if secondary is not None else "unavailable"
            ),
            "paired_excluding_unsure": (
                secondary["tasks"][task]["paired_annotations"] if secondary is not None else 0
            ),
        }
        if status != "PASS":
            entry["top_label_pairs_mixed_up"] = _top_confusions(primary["tasks"][task]["confusion_matrix"])
            entry["guide_sections_to_revisit"] = GUIDE_SECTIONS[task]
        verdicts[task] = entry

    statuses = {v["status"] for v in verdicts.values()}
    overall = "FAIL" if "FAIL" in statuses else "PASS" if statuses == {"PASS"} else "INCONCLUSIVE"
    decision = {
        "PASS": "The gate passed: you may scale up labeling under this guide version.",
        "FAIL": "The gate failed. Do NOT label more. Rewrite the confusable rules named below, "
                "freeze a new guide version, and relabel a fresh blind pilot.",
        "INCONCLUSIVE": "The gate cannot be decided yet. Fix the reasons listed per task "
                        "(too few pairs or a single-class rater) and run it again.",
    }[overall]

    informational = {
        task: {
            "paired_annotations": primary["tasks"][task]["paired_annotations"],
            "kappa": primary["tasks"][task]["kappa"],
            "raw_agreement": primary["tasks"][task]["raw_agreement"],
            "guide_sections": GUIDE_SECTIONS[task],
        }
        for task in ("priority", "category")
    }
    unsure_rows = sum(
        1 for r in v2_rows
        if (r.get("labeler") or "").strip().casefold() == "human"
        and (r.get("guide_version") or "").strip() == guide_version
        and (r.get("label_round") or "").strip().casefold() == "blind_relabel"
        and (r.get("unsure") or "").strip().casefold() == "yes"
    )
    return {
        "gate": "human_v2_agreement",
        "overall_status": overall,
        "decision": decision,
        "rule": {
            "gated_tasks": list(GATED_TASKS),
            "kappa_threshold": threshold,
            "minimum_paired_annotations": min_pairs,
            "guide_version": guide_version,
            "ground_truth": "Only labeler=human blind_relabel rows under this guide version count; minority batches, AI drafts, and unknown provenance are excluded.",
        "label_round": "blind_relabel",
        },
        "gated_tasks": verdicts,
        "informational_tasks_not_gating": informational,
        "category_view": "v2.1: legacy PERSONAL merged into OTHER for comparison only" if merge else "raw categories",
        "human_rows_marked_unsure": unsure_rows,
        "selection": primary["v2_selection"],
        "provenance_verified": primary["provenance_verified"],
        "matched_email_count": primary["matched_email_count"],
        "privacy_note": "Aggregate statistics only; no email IDs, senders, subjects, notes, or message text.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--legacy", type=Path, default=Path("data/email_labels.csv"))
    parser.add_argument("--v2", type=Path, default=Path("data/email_labels_v2.csv"))
    parser.add_argument("--guide-version", default="v2.1")
    parser.add_argument("--batch-name", action="append", default=None, help="Only use these batches (repeatable).")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    parser.add_argument("--min-pairs", type=int, default=DEFAULT_MIN_PAIRS)
    parser.add_argument("--bootstrap-iterations", type=int, default=2000)
    parser.add_argument("--output", type=Path, default=Path("reports/agreement_gate_v21.json"))
    parser.add_argument("--overwrite", action="store_true", help="Replace an existing report (a backup is made).")
    args = parser.parse_args(argv)

    try:
        _, legacy_rows = _read_csv(args.legacy)
        _, v2_rows = _read_csv(args.v2)
        report = evaluate_gate(
            legacy_rows, v2_rows, guide_version=args.guide_version, batch_names=args.batch_name,
            threshold=args.threshold, min_pairs=args.min_pairs,
            bootstrap_iterations=args.bootstrap_iterations,
        )
        if args.output.exists() and not args.overwrite:
            raise GateError(f"Report already exists: {args.output}. Pass --overwrite to replace it (a backup is made).")
    except (AgreementError, GateError) as exc:
        print(f"Agreement gate stopped: {exc}", file=sys.stderr)
        return 2

    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        _backup(args.output)
    temp = args.output.with_name(args.output.name + ".tmp")
    try:
        temp.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        temp.replace(args.output)
    finally:
        if temp.exists():
            temp.unlink()

    print(f"GATE: {report['overall_status']}")
    print(report["decision"])
    for task, entry in report["gated_tasks"].items():
        print(f"- {task}: {entry['status']} (n={entry['paired_annotations']}, kappa={entry['kappa']}, "
              f"raw agreement={entry['raw_agreement']}) {entry['reason']}")
        for pair in entry.get("top_label_pairs_mixed_up", []):
            print(f"    mixed up: legacy {pair['legacy_label']} -> v2 {pair['v2_label']} x{pair['count']}")
        if "guide_sections_to_revisit" in entry:
            print(f"    revisit: {entry['guide_sections_to_revisit']}")
    for task, entry in report["informational_tasks_not_gating"].items():
        print(f"- {task} (informational): n={entry['paired_annotations']}, kappa={entry['kappa']}")
    print(f"Aggregate-only report: {args.output}")
    return EXIT_CODES[report["overall_status"]]


if __name__ == "__main__":
    raise SystemExit(main())
