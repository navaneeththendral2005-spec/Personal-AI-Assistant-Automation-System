"""The text-plus-metadata pipeline extends, and never replaces, the existing baseline."""
import pickle

import numpy as np
import pytest
from sklearn.base import clone
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline

import backend.services.priority_model as priority_model
from backend.services.email_features import FEATURE_NAMES, FeatureContext, legacy_model_text
from backend.services.priority_model import (
    DEFAULT_METADATA_WEIGHT,
    VARIANTS,
    FeatureSchemaMismatchError,
    RowMetadataExtractor,
    ScaleBy,
    build_pipeline,
    train_model,
)
from scripts.evaluate_priority_engine import _features as existing_text_features


def _dataset(n=40):
    rows, labels = [], []
    for i in range(n):
        spam = i % 2 == 0
        rows.append({
            "sender": "Deals <news@mail.shop.com>" if spam else "Ravi <ravi@gmail.com>",
            "subject": f"Big sale {i}" if spam else f"Re: lunch plan {i}",
            "snippet": "discount unsubscribe" if spam else "can you confirm",
            "body_text": "buy now limited time offer" if spam else "please reply when free",
            "labels": "INBOX",
        })
        labels.append("yes" if spam else "no")
    return rows, labels


def _keys(rows):
    return [f"k{i}" for i in range(len(rows))]


def test_all_variants_build_fit_and_predict():
    rows, labels = _dataset()
    for variant in VARIANTS:
        model = train_model(rows, labels, keys=_keys(rows), frozen_test_ids=set(), variant=variant, task="is_spam")
        predictions = model.predict(rows)
        assert len(predictions) == len(rows)
        assert set(predictions) <= {"yes", "no"}
        probabilities = model.predict_proba(rows[:3])
        assert all(abs(sum(p.values()) - 1.0) < 1e-9 for p in probabilities)
        assert model.classes == ("no", "yes") and model.training_rows == len(rows)


def test_legacy_variant_reproduces_the_existing_baseline_exactly():
    rows, labels = _dataset()
    legacy = Pipeline([
        ("tfidf", TfidfVectorizer(ngram_range=(1, 2), min_df=1, max_features=30000, sublinear_tf=True)),
        ("classifier", LogisticRegression(max_iter=2000, class_weight="balanced", random_state=7)),
    ])
    legacy.fit([existing_text_features(row) for row in rows], labels)
    ours = train_model(rows, labels, keys=_keys(rows), frozen_test_ids=set(), variant="text_only_legacy", seed=7)
    assert ours.predict(rows) == list(legacy.predict([existing_text_features(row) for row in rows]))
    expected = legacy.predict_proba([existing_text_features(row) for row in rows])
    actual = ours.pipeline.predict_proba(rows)
    assert np.allclose(actual, expected)
    assert legacy_model_text(rows[0]) == existing_text_features(rows[0])


def test_metadata_variant_actually_uses_the_metadata():
    # Text is identical everywhere; only the recipient count carries the signal.
    rows, labels = [], []
    for i in range(48):
        bulk = i % 2 == 0
        recipients = ", ".join(f"u{j}@x.com" for j in range(8 if bulk else 1))
        rows.append({"sender": "a@x.com", "subject": "hello", "snippet": "same", "body_text": "same text", "recipient": recipients})
        labels.append("bulk" if bulk else "direct")
    train_rows, test_rows = rows[:32], rows[32:]
    train_labels, test_labels = labels[:32], labels[32:]
    keys = _keys(train_rows)

    with_metadata = train_model(train_rows, train_labels, keys=keys, frozen_test_ids=set(), variant="text_plus_metadata", metadata_weight=1.0)
    text_only = train_model(train_rows, train_labels, keys=keys, frozen_test_ids=set(), variant="text_only_normalized")
    meta_accuracy = np.mean([p == t for p, t in zip(with_metadata.predict(test_rows), test_labels)])
    text_accuracy = np.mean([p == t for p, t in zip(text_only.predict(test_rows), test_labels)])
    assert meta_accuracy == 1.0
    assert text_accuracy <= 0.5 + 1e-9


def test_training_and_inference_share_one_feature_definition():
    rows, labels = _dataset()
    context = FeatureContext(task="is_spam")
    model = train_model(rows, labels, keys=_keys(rows), frozen_test_ids=set(), task="is_spam")
    extractor = model.pipeline.named_steps["features"].transformer_list[1][1].named_steps["rows_to_features"]
    matrix = extractor.transform(rows)
    assert matrix.shape == (len(rows), len(FEATURE_NAMES))
    from backend.services.email_features import feature_matrix
    assert np.array_equal(matrix, np.asarray(feature_matrix(rows, context)))


def test_schema_change_blocks_prediction_until_retrained(monkeypatch):
    rows, labels = _dataset()
    model = train_model(rows, labels, keys=_keys(rows), frozen_test_ids=set())
    assert model.predict(rows[:2])
    monkeypatch.setattr(priority_model, "schema_fingerprint", lambda: "different-schema")
    with pytest.raises(FeatureSchemaMismatchError):
        model.predict(rows[:2])
    with pytest.raises(FeatureSchemaMismatchError):
        model.predict_proba(rows[:2])


def test_trained_model_survives_pickling():
    rows, labels = _dataset()
    model = train_model(rows, labels, keys=_keys(rows), frozen_test_ids=set(), task="is_spam")
    restored = pickle.loads(pickle.dumps(model))
    assert restored.predict(rows) == model.predict(rows)


def test_missing_metadata_columns_do_not_break_training_or_prediction():
    rows, labels = _dataset()
    sparse = [{"sender": r["sender"], "subject": r["subject"]} for r in rows]
    model = train_model(sparse, labels, keys=_keys(rows), frozen_test_ids=set(), task="is_spam")
    assert len(model.predict([{}, {"sender": None}, {"body_text": "x" * 100_000}])) == 3


def test_build_pipeline_validates_variant_and_is_clonable():
    with pytest.raises(ValueError):
        build_pipeline(variant="nope")
    for variant in VARIANTS:
        clone(build_pipeline(variant=variant))
    assert isinstance(clone(ScaleBy(0.25)), ScaleBy)
    assert clone(RowMetadataExtractor("is_spam")).task == "is_spam"


def test_invalid_metadata_configuration_fails_early():
    with pytest.raises(ValueError):
        RowMetadataExtractor(gmail_label_mode="all-labels").fit([{}])


def test_default_metadata_weight_is_documented_constant():
    assert 0 < DEFAULT_METADATA_WEIGHT <= 1
    pipeline = build_pipeline(variant="text_plus_metadata")
    weight = pipeline.named_steps["features"].transformer_list[1][1].named_steps["weight"]
    assert weight.weight == DEFAULT_METADATA_WEIGHT
