"""The pre-registered kappa gate and the provenance filters of the agreement script."""
from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from scripts import agreement_gate as gate_module
from scripts.agreement_gate import GateError, evaluate_gate
from scripts.compare_label_agreement_v2 import AgreementError, compare_label_rows

SECRET_ID, SECRET_NOTE = "SECRET-ID-9", "SECRET NOTE TEXT"


def legacy(i, spam="no", resp="no", priority="INFORMATIONAL", category="JOB"):
    return {"email_id": f"e{i}", "true_is_spam": spam, "true_needs_response": resp,
            "true_priority": priority, "true_category": category}


def v2(i, spam="no", resp="no", priority="INFORMATIONAL", category="JOB", *, unsure="no",
       guide="v2.1", batch="pilot", labeler="human", note=""):
    return {"email_id": f"e{i}", "is_spam": spam, "needs_response": resp, "priority": priority,
            "category": category, "unsure": unsure, "note": note, "guide_version": guide,
            "batch_name": batch, "labeler": labeler}


def dataset(n=40, spam_every=4, resp_every=3, flip_spam=(), flip_resp=()):
    """Legacy labels plus human labels that copy them except for the flipped indexes."""
    old, new = [], []
    for i in range(n):
        spam = "yes" if i % spam_every == 0 else "no"
        resp = "yes" if i % resp_every == 0 else "no"
        old.append(legacy(i, spam, resp))
        flip = lambda value, flag: ("no" if value == "yes" else "yes") if flag else value  # noqa: E731
        new.append(v2(i, flip(spam, i in flip_spam), flip(resp, i in flip_resp)))
    return old, new


def test_pass_when_both_gated_tasks_reach_the_threshold():
    old, new = dataset(flip_spam=(1,), flip_resp=(2,))
    report = evaluate_gate(old, new)
    assert report["overall_status"] == "PASS"
    assert {t["status"] for t in report["gated_tasks"].values()} == {"PASS"}
    assert all(t["kappa"] >= 0.6 for t in report["gated_tasks"].values())
    assert report["provenance_verified"] is True and "scale up" in report["decision"]
    assert "top_label_pairs_mixed_up" not in report["gated_tasks"]["is_spam"]


def test_fail_names_the_confusable_pairs_and_guide_sections_and_never_says_label_more():
    old, new = dataset(flip_spam=tuple(range(0, 40, 2)))  # half the spam decisions differ
    report = evaluate_gate(old, new)
    spam = report["gated_tasks"]["is_spam"]
    assert spam["status"] == "FAIL" and report["overall_status"] == "FAIL"
    assert spam["kappa"] < 0.6
    assert spam["top_label_pairs_mixed_up"] and spam["top_label_pairs_mixed_up"][0]["count"] > 0
    assert all(p["legacy_label"] != p["v2_label"] for p in spam["top_label_pairs_mixed_up"])
    assert "sections 3.1 and 5.1" in spam["guide_sections_to_revisit"]
    assert "Do NOT label more" in report["decision"]


def test_too_few_pairs_is_inconclusive_not_a_pass_or_fail():
    old, new = dataset(n=12, spam_every=3, resp_every=2)
    report = evaluate_gate(old, new, min_pairs=20)
    assert report["overall_status"] == "INCONCLUSIVE"
    assert all(t["status"] == "INCONCLUSIVE" and "at least 20" in t["reason"] for t in report["gated_tasks"].values())


def test_single_class_human_rater_is_inconclusive_with_an_explanation():
    old, _ = dataset()
    new = [v2(i) for i in range(40)]  # human says "no" to everything
    report = evaluate_gate(old, new)
    spam = report["gated_tasks"]["is_spam"]
    assert spam["status"] == "INCONCLUSIVE" and spam["kappa"] == "undefined_degenerate"
    assert "single class" in spam["reason"] and report["overall_status"] == "INCONCLUSIVE"


def test_any_fail_beats_inconclusive():
    old, new = dataset(flip_spam=tuple(range(0, 40, 2)))
    for row in new:
        row["needs_response"] = "no"  # needs_response becomes degenerate
    report = evaluate_gate(old, new)
    assert report["gated_tasks"]["is_spam"]["status"] == "FAIL"
    assert report["gated_tasks"]["needs_response"]["status"] == "INCONCLUSIVE"
    assert report["overall_status"] == "FAIL"


def test_threshold_and_min_pairs_are_validated():
    old, new = dataset()
    with pytest.raises(GateError):
        evaluate_gate(old, new, threshold=0)
    with pytest.raises(GateError):
        evaluate_gate(old, new, min_pairs=1)


def test_threshold_is_configurable_but_the_default_is_point_six():
    old, new = dataset(flip_spam=(1, 5, 9), flip_resp=(2, 5))
    assert evaluate_gate(old, new)["rule"]["kappa_threshold"] == 0.6
    strict = evaluate_gate(old, new, threshold=0.99)
    assert strict["overall_status"] == "FAIL"


# ---- provenance ---------------------------------------------------------------

def test_missing_labeler_column_is_refused_because_provenance_is_unknown():
    old, new = dataset()
    for row in new:
        del row["labeler"]
    with pytest.raises(GateError, match="labeler"):
        evaluate_gate(old, new)


def test_ai_draft_rows_are_excluded_and_counted_never_scored():
    old, new = dataset()
    drafts = [v2(i, spam="yes", resp="yes", labeler="ai_draft", batch="draft_batch") for i in range(40)]
    report = evaluate_gate(old, new + drafts)
    assert report["selection"]["excluded_non_human_rows"] == 40
    assert report["selection"]["rows_by_labeler"] == {"ai_draft": 40, "human": 40}
    assert report["matched_email_count"] == 40 and report["overall_status"] == "PASS"


def test_a_draft_note_on_a_human_row_is_refused():
    old, new = dataset()
    new[3]["note"] = "claude_draft: slipped through"
    with pytest.raises(GateError, match="AI-draft"):
        evaluate_gate(old, new)


def test_only_the_requested_guide_version_is_used():
    old, new = dataset()
    older = [v2(i, spam="yes", guide="v2.0-draft", batch="old") for i in range(40)]
    report = evaluate_gate(old, new + older, guide_version="v2.1")
    assert report["selection"]["guide_versions_in_selection"] == ["v2.1"]
    assert report["selection"]["excluded_by_filter"] == 40


def test_two_human_rounds_need_a_batch_choice_with_a_helpful_error():
    old, new = dataset()
    second_round = [v2(i, batch="recheck") for i in range(40)]
    with pytest.raises(GateError, match="--batch-name"):
        evaluate_gate(old, new + second_round)
    report = evaluate_gate(old, new + second_round, batch_names=["pilot"])
    assert report["selection"]["batches_in_selection"] == ["pilot"]


def test_unknown_guide_version_leaves_nothing_to_compare():
    old, new = dataset()
    with pytest.raises(GateError, match="No v2 annotations remain"):
        evaluate_gate(old, new, guide_version="v9.9")


def test_unsure_rows_are_reported_and_excluded_in_a_second_view():
    old, new = dataset(flip_spam=(1, 2, 3, 5), flip_resp=())
    for i in (1, 2, 3, 5):
        new[i]["unsure"] = "yes"
    report = evaluate_gate(old, new)
    spam = report["gated_tasks"]["is_spam"]
    assert report["human_rows_marked_unsure"] == 4
    assert spam["paired_excluding_unsure"] == 36 and spam["kappa_excluding_unsure"] > spam["kappa"]


def test_report_contains_no_ids_or_notes():
    old, new = dataset()
    new[0]["email_id"] = old[0]["email_id"] = SECRET_ID
    new[1]["note"] = SECRET_NOTE
    text = json.dumps(evaluate_gate(old, new))
    assert SECRET_ID not in text and SECRET_NOTE not in text


# ---- compare_label_rows additions ---------------------------------------------

def test_category_view_merges_legacy_personal_into_other_for_v21_only():
    old = [legacy(i, category="PERSONAL" if i % 2 else "JOB") for i in range(20)]
    new = [v2(i, category="OTHER" if i % 2 else "JOB") for i in range(20)]
    merged = compare_label_rows(old, new, merge_personal_into_other=True)["tasks"]["category"]
    raw = compare_label_rows(old, new)["tasks"]["category"]
    assert merged["raw_agreement"] == 1.0 and "PERSONAL" not in merged["confusion_matrix"]
    assert raw["raw_agreement"] == 0.5 and "category_view" in merged
    # Stored labels are untouched: the merge is a comparison-time view only.
    assert old[1]["true_category"] == "PERSONAL"


def test_confusion_matrix_rows_are_legacy_and_columns_are_v2():
    old = [legacy(i, spam="yes" if i < 6 else "no") for i in range(10)]
    new = [v2(i, spam="no") for i in range(10)]
    matrix = compare_label_rows(old, new)["tasks"]["is_spam"]["confusion_matrix"]
    assert matrix == {"no": {"no": 4, "yes": 0}, "yes": {"no": 6, "yes": 0}}


def test_provenance_report_fields_and_default_exclusion_of_non_human_rows():
    old, new = dataset(n=20)
    mixed = new + [v2(i, labeler="ai_draft", batch="d") for i in range(20)]
    report = compare_label_rows(old, mixed)
    assert report["provenance_verified"] is True
    assert report["v2_selection"]["excluded_non_human_rows"] == 20
    for row in new:
        del row["labeler"]
    assert compare_label_rows(old, new)["provenance_verified"] is False  # unknown provenance is flagged
    with pytest.raises(AgreementError, match="labeler"):
        compare_label_rows(old, new, require_human_provenance=True)


# ---- CLI ----------------------------------------------------------------------

def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def run_cli(tmp_path, old, new, *extra):
    legacy_path, v2_path = tmp_path / "legacy.csv", tmp_path / "v2.csv"
    write_csv(legacy_path, old)
    write_csv(v2_path, new)
    return gate_module.main(["--legacy", str(legacy_path), "--v2", str(v2_path),
                             "--output", str(tmp_path / "gate.json"), *extra])


def test_cli_exit_codes_match_the_verdict(tmp_path, capsys):
    old, good = dataset(flip_spam=(1,), flip_resp=(2,))
    assert run_cli(tmp_path, old, good) == 0
    assert "GATE: PASS" in capsys.readouterr().out
    _, bad = dataset(flip_spam=tuple(range(0, 40, 2)))
    assert run_cli(tmp_path, old, bad, "--overwrite") == 1
    out = capsys.readouterr().out
    assert "GATE: FAIL" in out and "mixed up:" in out and "revisit:" in out
    degenerate = [v2(i) for i in range(40)]
    assert run_cli(tmp_path, old, degenerate, "--overwrite") == 3


def test_cli_refuses_to_overwrite_a_report_and_backs_up_on_overwrite(tmp_path, capsys):
    old, new = dataset(flip_spam=(1,), flip_resp=(2,))
    assert run_cli(tmp_path, old, new) == 0
    first = (tmp_path / "gate.json").read_bytes()
    assert run_cli(tmp_path, old, new) == 2
    assert "already exists" in capsys.readouterr().err
    assert (tmp_path / "gate.json").read_bytes() == first
    assert run_cli(tmp_path, old, new, "--overwrite") == 0
    assert len(list((tmp_path / "backups").glob("gate.*.json"))) == 1


def test_cli_output_has_no_ids_or_notes_and_errors_exit_two(tmp_path, capsys):
    old, new = dataset(flip_spam=(1,), flip_resp=(2,))
    new[0]["note"], new[0]["email_id"], old[0]["email_id"] = SECRET_NOTE, SECRET_ID, SECRET_ID
    assert run_cli(tmp_path, old, new) == 0
    captured = capsys.readouterr()
    assert SECRET_NOTE not in captured.out + captured.err and SECRET_ID not in captured.out + captured.err
    assert SECRET_NOTE not in (tmp_path / "gate.json").read_text(encoding="utf-8")
    for row in new:
        del row["labeler"]
    assert run_cli(tmp_path, old, new, "--overwrite") == 2
    assert "labeler" in capsys.readouterr().err
    assert gate_module.main(["--legacy", str(tmp_path / "nope.csv"), "--v2", str(tmp_path / "v2.csv")]) == 2
