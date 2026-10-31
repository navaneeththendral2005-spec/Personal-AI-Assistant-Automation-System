"""Ground-truth leakage prevention: labels, predictions, and frozen rows must never
reach a model input, and Gmail labels follow a task-specific safe-use policy."""
import json

import pytest

from backend.services.email_features import (
    ALLOWED_INPUT_COLUMNS,
    ASSISTED_LABELS_BY_TASK,
    FEATURE_NAMES,
    GOOGLE_CLASSIFIER_LABELS,
    NEVER_SAFE_GMAIL_LABELS,
    FeatureContext,
    FeatureLeakageError,
    assert_no_forbidden_columns,
    extract_features,
    feature_vector,
    find_forbidden_columns,
    legacy_model_text,
    model_input_view,
    normalized_model_text,
    safe_gmail_labels,
)
from backend.services.priority_model import (
    RowMetadataExtractor,
    RowTextExtractor,
    TrainingLeakageError,
    assert_training_excludes_frozen,
    build_pipeline,
    train_model,
)

BASE_ROW = {
    "email_id": "abc123",
    "sender": "Ravi <ravi@gmail.com>",
    "subject": "Re: lunch?",
    "snippet": "can you confirm",
    "body_text": "Hi Navaneeth, can you confirm tomorrow?",
    "labels": "INBOX|CATEGORY_PERSONAL|STARRED",
    "recipient": "me@x.com",
    "message_date": "2026-10-20T10:00:00Z",
}
LEAKY_COLUMNS = {
    "true_priority": "URGENT",
    "true_is_spam": "yes",
    "true_needs_response": "yes",
    "true_category": "FINANCE",
    "final_true_priority": "NEEDS_REPLY",
    "final_true_category": "JOB",
    "predicted_priority": "URGENT",
    "predicted_is_spam": "yes",
    "rules_priority": "INFORMATIONAL",
    "rules_category": "PROMOTIONS",
    "llm_priority": "URGENT",
    "llm_reason": "looks urgent because",
    "llm_confidence": "0.99",
    "label_source": "human",
    "review_reason": "model_disagreement",
    "needs_review": "yes",
    "decision_reason": "adjudicated",
}


# --------------------------------------------------------------------------- #
# Truth / prediction columns can never change a feature
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("task", [None, "priority", "is_spam", "needs_response", "category"])
@pytest.mark.parametrize("mode", ["independent", "gmail_assisted"])
def test_forbidden_columns_cannot_influence_features(task, mode):
    context = FeatureContext(task=task, gmail_label_mode=mode)
    clean = feature_vector(BASE_ROW, context)
    for column, value in LEAKY_COLUMNS.items():
        assert feature_vector({**BASE_ROW, column: value}, context) == clean, column
    # Flip every leaky column to a different value at once.
    flipped = {column: ("FLIPPED" if value != "FLIPPED" else "x") for column, value in LEAKY_COLUMNS.items()}
    assert feature_vector({**BASE_ROW, **flipped}, context) == clean


def test_model_text_ignores_forbidden_columns():
    leaky = {**BASE_ROW, **LEAKY_COLUMNS}
    assert legacy_model_text(leaky) == legacy_model_text(BASE_ROW)
    assert normalized_model_text(leaky) == normalized_model_text(BASE_ROW)


def test_email_id_is_not_a_model_input():
    assert "email_id" not in model_input_view(BASE_ROW)
    assert feature_vector({**BASE_ROW, "email_id": "different"}) == feature_vector(BASE_ROW)


def test_model_input_view_contains_only_allow_listed_columns():
    view = model_input_view({**BASE_ROW, **LEAKY_COLUMNS, "unknown_column": 1})
    assert set(view) <= set(ALLOWED_INPUT_COLUMNS)
    assert not find_forbidden_columns(view)
    assert model_input_view(None) == {}  # type: ignore[arg-type]


def test_no_feature_name_encodes_a_label_or_prediction():
    assert not find_forbidden_columns(FEATURE_NAMES)
    assert not any(name.lower().startswith(("true", "pred", "rules", "llm")) for name in FEATURE_NAMES)


def test_allow_list_never_overlaps_forbidden_prefixes():
    assert find_forbidden_columns(ALLOWED_INPUT_COLUMNS) == []


def test_forbidden_column_detection_and_assertion():
    assert set(find_forbidden_columns(["sender", "true_priority", "rules_x", "LLM_status", "final_true_category"])) == {
        "true_priority", "rules_x", "LLM_status", "final_true_category",
    }
    assert_no_forbidden_columns(["sender", "subject"])
    with pytest.raises(FeatureLeakageError):
        assert_no_forbidden_columns(["sender", "predicted_priority"])


def test_row_extractors_ignore_truth_columns_inside_pipeline_inputs():
    leaky = {**BASE_ROW, **LEAKY_COLUMNS}
    assert RowTextExtractor("legacy").fit([leaky]).transform([leaky]) == RowTextExtractor("legacy").fit([BASE_ROW]).transform([BASE_ROW])
    left = RowMetadataExtractor("is_spam").fit([leaky]).transform([leaky])
    right = RowMetadataExtractor("is_spam").fit([BASE_ROW]).transform([BASE_ROW])
    assert (left == right).all()


# --------------------------------------------------------------------------- #
# Task-specific Gmail label policy
# --------------------------------------------------------------------------- #

ALL_LABELS = GOOGLE_CLASSIFIER_LABELS | NEVER_SAFE_GMAIL_LABELS | {"MY_CUSTOM_LABEL", "JOBS"}


@pytest.mark.parametrize("task", [None, "priority", "is_spam", "needs_response", "category"])
def test_independent_mode_allows_no_gmail_labels(task):
    assert safe_gmail_labels(ALL_LABELS, task, "independent") == frozenset()
    features = extract_features({"labels": "|".join(ALL_LABELS)}, FeatureContext(task=task))
    assert features["gmail_labels_available"] == 0.0
    assert not any(value for name, value in features.items() if name.startswith("gmail_label_"))


@pytest.mark.parametrize("task", ["priority", "is_spam", "needs_response", "category"])
def test_assisted_mode_never_allows_behaviour_or_custom_labels(task):
    allowed = safe_gmail_labels(ALL_LABELS, task, "gmail_assisted")
    assert allowed <= ASSISTED_LABELS_BY_TASK[task]
    assert not allowed & NEVER_SAFE_GMAIL_LABELS
    assert "MY_CUSTOM_LABEL" not in allowed and "JOBS" not in allowed


def test_assisted_mode_policy_differs_by_task():
    assert "SPAM" in safe_gmail_labels({"SPAM"}, "is_spam", "gmail_assisted")
    for task in ("priority", "needs_response", "category"):
        assert "SPAM" not in safe_gmail_labels({"SPAM"}, task, "gmail_assisted")
    assert "IMPORTANT" in safe_gmail_labels({"IMPORTANT"}, "needs_response", "gmail_assisted")
    assert "IMPORTANT" not in safe_gmail_labels({"IMPORTANT"}, "category", "gmail_assisted")


def test_starred_and_unread_never_become_features_in_any_mode():
    row = {"labels": "STARRED|UNREAD|SENT|DRAFT|TRASH"}
    for mode in ("independent", "gmail_assisted"):
        features = extract_features(row, FeatureContext(task="needs_response", gmail_label_mode=mode))
        assert features["gmail_labels_available"] == 0.0


def test_gmail_label_features_change_only_in_assisted_mode():
    row = {**BASE_ROW, "labels": "SPAM|CATEGORY_PROMOTIONS"}
    independent = feature_vector(row, FeatureContext(task="is_spam"))
    without_labels = feature_vector({**row, "labels": ""}, FeatureContext(task="is_spam"))
    assert independent == without_labels
    assisted = extract_features(row, FeatureContext(task="is_spam", gmail_label_mode="gmail_assisted"))
    assert assisted["gmail_label_SPAM"] == 1.0 and assisted["gmail_label_CATEGORY_PROMOTIONS"] == 1.0


def test_invalid_mode_or_task_is_rejected():
    with pytest.raises(ValueError):
        safe_gmail_labels({"SPAM"}, "is_spam", "everything")
    with pytest.raises(ValueError):
        FeatureContext(gmail_label_mode="everything")
    with pytest.raises(ValueError):
        FeatureContext(task="not_a_task")


# --------------------------------------------------------------------------- #
# Training never touches frozen test rows
# --------------------------------------------------------------------------- #

def _rows_and_labels(n=12):
    rows, labels = [], []
    for i in range(n):
        spam = i % 2 == 0
        rows.append({
            "sender": "Deals <news@mail.shop.com>" if spam else "Ravi <ravi@gmail.com>",
            "subject": f"Sale {i}" if spam else f"Re: lunch {i}",
            "snippet": "discount unsubscribe" if spam else "can you confirm",
            "body_text": "buy now" if spam else "please reply",
            "true_is_spam": "yes" if spam else "no",
        })
        labels.append("yes" if spam else "no")
    return rows, labels


def test_assert_training_excludes_frozen_detects_overlap():
    assert_training_excludes_frozen(["a", "b"], {"c"})
    assert_training_excludes_frozen(["a"], None)
    assert_training_excludes_frozen(["a"], set())
    with pytest.raises(TrainingLeakageError):
        assert_training_excludes_frozen(["a", "b"], {"b"})


def test_train_model_refuses_frozen_rows_and_bad_inputs():
    rows, labels = _rows_and_labels()
    keys = [f"k{i}" for i in range(len(rows))]
    with pytest.raises(TrainingLeakageError):
        train_model(rows, labels, keys=keys, frozen_test_ids={"k3"}, task="is_spam")
    with pytest.raises(ValueError):
        train_model(rows, labels[:-1], keys=keys, frozen_test_ids=set())
    with pytest.raises(ValueError):
        train_model(rows, ["yes"] * len(rows), keys=keys, frozen_test_ids=set())


def test_predictions_do_not_depend_on_truth_columns_of_the_rows_being_scored():
    rows, labels = _rows_and_labels()
    keys = [f"k{i}" for i in range(len(rows))]
    model = train_model(rows, labels, keys=keys, frozen_test_ids=set(), task="is_spam")
    scored = [dict(row) for row in rows]
    flipped = [{**row, "true_is_spam": "no" if row["true_is_spam"] == "yes" else "yes", "predicted_is_spam": "zzz"} for row in rows]
    assert model.predict(scored) == model.predict(flipped)
    assert model.predict_proba(scored) == model.predict_proba(flipped)


def test_pipeline_fit_receives_no_truth_columns(monkeypatch):
    seen = {}
    pipeline = build_pipeline(variant="text_plus_metadata", task="is_spam")
    original_fit = type(pipeline).fit

    def spy(self, X, y=None, **kwargs):
        seen["columns"] = {column for row in X for column in row}
        return original_fit(self, X, y, **kwargs)

    monkeypatch.setattr(type(pipeline), "fit", spy)
    rows, labels = _rows_and_labels()
    train_model(rows, labels, keys=[f"k{i}" for i in range(len(rows))], frozen_test_ids=set(), task="is_spam")
    assert seen["columns"] and not find_forbidden_columns(seen["columns"])
    assert seen["columns"] <= set(ALLOWED_INPUT_COLUMNS)


def test_feature_pipeline_evaluation_never_trains_on_frozen_rows_and_keeps_manifest_read_only(tmp_path, monkeypatch):
    import csv

    from scripts import evaluate_feature_pipeline as module

    rows = []
    for i in range(40):
        spam = i % 2 == 0
        rows.append({
            "email_id": f"id{i:03d}",
            "sender": "Deals <news@mail.shop.com>" if spam else "Ravi <ravi@gmail.com>",
            "subject": f"Sale {i}" if spam else f"Re: lunch {i}",
            "snippet": "discount" if spam else "confirm",
            "body_text": "buy now unsubscribe" if spam else "please reply",
            "labels": "INBOX",
            "true_is_spam": "yes" if spam else "no",
        })
    data = tmp_path / "data.csv"
    with data.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    frozen = {f"id{i:03d}" for i in range(0, 40, 4)}
    manifest = tmp_path / "split.json"
    manifest.write_text(json.dumps({"version": 1, "tasks": {"is_spam": {"test_ids": sorted(frozen)}}}), encoding="utf-8")
    before = manifest.read_bytes()

    trained_on = []
    original = module.train_model

    def spy(rows_arg, labels_arg, *, keys, **kwargs):
        trained_on.append(set(keys))
        return original(rows_arg, labels_arg, keys=keys, **kwargs)

    monkeypatch.setattr(module, "train_model", spy)
    report = module.evaluate_feature_pipeline(data, manifest, tasks=("is_spam",), bootstrap=20)

    assert trained_on, "the evaluation must train at least one model"
    assert all(not (keys & frozen) for keys in trained_on)
    assert manifest.read_bytes() == before
    assert report["tasks"]["is_spam"]["dataset"]["frozen_test"] == len(frozen)
    assert report["leakage_controls"]["frozen_manifest_is_read_only"] is True
