"""Feature-pipeline comparison: protocol, privacy, determinism, and failure handling."""
import csv
import json

import pytest

from scripts import evaluate_feature_pipeline as module
from scripts.evaluate_feature_pipeline import (
    FeatureEvaluationError,
    evaluate_feature_pipeline,
    main,
    read_frozen_test_ids,
    recommend_variant,
    render_markdown,
    verified_rows,
)

SECRET_SENDER = "Distinctive Person <distinctive.person@private-example.org>"
SECRET_SUBJECT = "Confidential subject marker 8841"


def _write_csv(path, rows):
    fields = list(rows[0])
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _rows(n=48):
    rows = []
    for i in range(n):
        spam = i % 2 == 0
        needs = i % 3 == 0
        rows.append({
            "email_id": f"id{i:03d}",
            "sender": "Deals <news@mail.shop.com>" if spam else (SECRET_SENDER if i == 1 else "Ravi <ravi@gmail.com>"),
            "subject": f"Sale {i}" if spam else (SECRET_SUBJECT if i == 1 else f"Re: lunch {i}"),
            "snippet": "discount unsubscribe" if spam else "can you confirm",
            "body_text": "buy now limited offer" if spam else "please reply when free",
            "labels": "INBOX",
            "true_priority": "INFORMATIONAL" if spam else ("NEEDS_REPLY" if needs else "URGENT"),
            "true_is_spam": "yes" if spam else "no",
            "true_needs_response": "yes" if needs else "no",
            "true_category": "PROMOTIONS" if spam else ("PERSONAL" if needs else "FINANCE"),
            "predicted_is_spam": "yes", "rules_priority": "URGENT", "llm_category": "JOB",
        })
    return rows


def _manifest(path, tasks_ids):
    path.write_text(
        json.dumps({"version": 1, "tasks": {task: {"test_ids": sorted(ids)} for task, ids in tasks_ids.items()}}),
        encoding="utf-8",
    )


@pytest.fixture()
def dataset(tmp_path):
    rows = _rows()
    data = tmp_path / "data.csv"
    _write_csv(data, rows)
    frozen = {f"id{i:03d}" for i in range(0, 48, 4)}
    manifest = tmp_path / "split.json"
    _manifest(manifest, {task: frozen for task in ("priority", "is_spam", "needs_response", "category")})
    return data, manifest, frozen


def test_report_structure_and_all_variants_present(dataset):
    data, manifest, frozen = dataset
    report = evaluate_feature_pipeline(data, manifest, bootstrap=20)
    assert set(report["tasks"]) == {"priority", "is_spam", "needs_response", "category"}
    entry = report["tasks"]["is_spam"]
    assert entry["status"] == "ok" and entry["dataset"]["frozen_test"] == len(frozen)
    assert entry["dataset"]["verified"] == entry["dataset"]["development"] + entry["dataset"]["frozen_test"]
    cv = entry["development_cross_validation"]
    assert cv["status"] == "ok"
    expected = {name for name, _, _ in module.EXPERIMENTS}
    assert set(cv["variants"]) == expected
    assert set(entry["frozen_test"]["variants"]) == expected
    assert entry["frozen_test"]["paired_bootstrap_vs_baseline"].keys() == expected - {module.BASELINE}
    assert entry["recommendation"]["variant"] in expected
    assert report["leakage_controls"]["forbidden_columns_present_in_csv"] >= 4
    assert report["leakage_controls"]["forbidden_columns_reach_features"] is False


def test_report_contains_no_email_content_or_identifiers(dataset):
    data, manifest, _ = dataset
    report = evaluate_feature_pipeline(data, manifest, bootstrap=20)
    text = json.dumps(report) + render_markdown(report)
    for secret in (SECRET_SENDER, SECRET_SUBJECT, "distinctive.person", "private-example", "id001", "id004"):
        assert secret not in text


def test_evaluation_is_deterministic(dataset):
    data, manifest, _ = dataset
    first = evaluate_feature_pipeline(data, manifest, seed=3, bootstrap=30)
    second = evaluate_feature_pipeline(data, manifest, seed=3, bootstrap=30)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_predictions_columns_never_change_results(tmp_path, dataset):
    data, manifest, _ = dataset
    baseline = evaluate_feature_pipeline(data, manifest, bootstrap=20)
    mutated = [{**row, "predicted_is_spam": "no", "rules_priority": "INFORMATIONAL", "llm_category": "OTHER"} for row in _rows()]
    other = tmp_path / "mutated.csv"
    _write_csv(other, mutated)
    changed = evaluate_feature_pipeline(other, manifest, bootstrap=20)
    for task in baseline["tasks"]:
        assert baseline["tasks"][task]["frozen_test"]["variants"] == changed["tasks"][task]["frozen_test"]["variants"]


def test_missing_manifest_is_not_created_and_only_cv_is_reported(tmp_path):
    data = tmp_path / "data.csv"
    _write_csv(data, _rows())
    manifest = tmp_path / "split.json"
    report = evaluate_feature_pipeline(data, manifest, tasks=("is_spam",), bootstrap=10)
    assert not manifest.exists()
    entry = report["tasks"]["is_spam"]
    assert entry["frozen_split"] == "not_found"
    assert entry["frozen_test"]["status"] == "no_frozen_test_rows"
    assert entry["development_cross_validation"]["status"] == "ok"


def test_manifest_without_the_task_is_left_untouched(tmp_path):
    data = tmp_path / "data.csv"
    _write_csv(data, _rows())
    manifest = tmp_path / "split.json"
    _manifest(manifest, {"priority": {"id000"}})
    before = manifest.read_bytes()
    report = evaluate_feature_pipeline(data, manifest, tasks=("is_spam",), bootstrap=10)
    assert manifest.read_bytes() == before
    assert report["tasks"]["is_spam"]["frozen_split"] == "not_found"


@pytest.mark.parametrize("content", ["not json", json.dumps({"version": 99, "tasks": {}}), json.dumps({"version": 1, "tasks": []}),
                                     json.dumps({"version": 1, "tasks": {"is_spam": {"test_ids": "oops"}}})])
def test_malformed_manifest_is_rejected_without_modification(tmp_path, content):
    manifest = tmp_path / "split.json"
    manifest.write_text(content, encoding="utf-8")
    with pytest.raises(FeatureEvaluationError):
        read_frozen_test_ids(manifest, "is_spam")
    assert manifest.read_text(encoding="utf-8") == content


def test_insufficient_labels_and_missing_columns_are_reported_not_crashing(tmp_path):
    rows = [{"email_id": "a", "sender": "x@y.com", "subject": "s", "snippet": "", "body_text": "", "labels": "", "true_is_spam": "yes"}]
    data = tmp_path / "tiny.csv"
    _write_csv(data, rows)
    report = evaluate_feature_pipeline(data, tmp_path / "none.json", bootstrap=5)
    assert all(entry["status"] == "insufficient_verified_labels" for entry in report["tasks"].values())
    assert "insufficient_verified_labels" in render_markdown(report)


def test_missing_dataset_raises_clear_error(tmp_path):
    with pytest.raises(FeatureEvaluationError):
        evaluate_feature_pipeline(tmp_path / "missing.csv", tmp_path / "split.json")
    empty = tmp_path / "empty.csv"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(FeatureEvaluationError):
        evaluate_feature_pipeline(empty, tmp_path / "split.json")


def test_unknown_task_is_rejected(dataset):
    data, manifest, _ = dataset
    with pytest.raises(FeatureEvaluationError):
        evaluate_feature_pipeline(data, manifest, tasks=("nope",))


def test_legacy_spam_rows_are_excluded_from_the_priority_task():
    rows = [
        {"email_id": "a", "true_priority": "SPAM"}, {"email_id": "b", "true_priority": "URGENT"},
        {"email_id": "c", "true_priority": "bogus"}, {"email_id": "d", "true_priority": ""},
    ]
    assert [item["truth"] for item in verified_rows(rows, "priority")] == ["URGENT"]


def test_small_test_set_warning_and_interpretation(dataset):
    data, manifest, _ = dataset
    report = evaluate_feature_pipeline(data, manifest, tasks=("is_spam",), bootstrap=20)
    notes = " ".join(report["tasks"]["is_spam"]["interpretation"])
    assert "only 12 rows" in notes and "exploratory" in notes


def test_recommendation_uses_development_cv_only_and_margin():
    def cv(scores):
        return {"status": "ok", "variants": {name: {"macro_f1": value} for name, value in scores.items()}}

    better = recommend_variant(cv({"text_only_legacy": 0.50, "text_only_normalized": 0.50, "text_plus_metadata": 0.60}))
    assert better["variant"] == "text_plus_metadata" and better["macro_f1_gain"] == pytest.approx(0.10)
    small = recommend_variant(cv({"text_only_legacy": 0.50, "text_only_normalized": 0.50, "text_plus_metadata": 0.52}))
    assert small["variant"] == "text_only_legacy"
    ablation_only = recommend_variant(cv({"text_only_legacy": 0.40, "text_plus_metadata_gmail_assisted": 0.90}))
    assert ablation_only["variant"] == "text_only_legacy"  # the Gmail-assisted ablation is never recommended
    assert recommend_variant({"status": "insufficient_data_for_cross_validation"})["variant"] == "text_only_legacy"


def test_cli_writes_json_and_markdown(dataset, tmp_path, capsys):
    data, manifest, _ = dataset
    out_json, out_md = tmp_path / "out" / "r.json", tmp_path / "out" / "r.md"
    code = main(["--input", str(data), "--manifest", str(manifest), "--output-json", str(out_json),
                 "--output-md", str(out_md), "--bootstrap", "10", "--task", "is_spam"])
    assert code == 0 and out_json.is_file() and out_md.is_file()
    assert "FRIDAY feature-pipeline evaluation" in out_md.read_text(encoding="utf-8")
    assert "aggregate numbers only" in capsys.readouterr().out
    assert main(["--input", str(tmp_path / "missing.csv"), "--output-json", str(out_json), "--output-md", str(out_md)]) == 2
