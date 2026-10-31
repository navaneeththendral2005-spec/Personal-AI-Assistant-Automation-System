"""Text-plus-metadata classifier pipeline for FRIDAY's priority engine.

This extends (it does not replace) the existing TF-IDF + logistic-regression
baseline used by ``scripts.evaluate_priority_engine``:

* ``text_only_legacy`` reproduces the existing baseline exactly (same text builder,
  same TF-IDF and logistic-regression settings).
* ``text_only_normalized`` keeps the same model but feeds it normalised text, so the
  effect of normalisation can be measured on its own.
* ``text_plus_metadata`` adds the structured features from ``email_features`` next
  to the TF-IDF text features in a single scikit-learn pipeline.

Training and inference share ``email_features`` and every trained model records a
schema fingerprint, so a feature definition can never silently drift between the
two. ``train_model`` also refuses to fit on any frozen test row.

scikit-learn is an *optional* dependency (``requirements-ml.txt``); only offline
scripts and tests import this module, never the running API.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Mapping, Sequence

try:  # pragma: no cover - exercised implicitly by every test that imports this
    import numpy as np
    from sklearn.base import BaseEstimator, TransformerMixin
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import FeatureUnion, Pipeline
    from sklearn.preprocessing import MaxAbsScaler
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "scikit-learn is required for the priority model. "
        "Install it with: pip install -r requirements-ml.txt"
    ) from exc

from backend.services.email_features import (
    FEATURE_NAMES,
    FeatureContext,
    feature_matrix,
    legacy_model_text,
    model_input_view,
    normalized_model_text,
    schema_fingerprint,
)

VARIANTS = ("text_only_legacy", "text_only_normalized", "text_plus_metadata")
# Chosen with a small sweep on development cross-validation only (never the frozen
# test set); see docs/PRIORITY_ENGINE_FEATURES.md. MaxAbsScaler keeps binary flags in
# [0, 1], so one rare feature cannot be inflated the way StandardScaler would.
DEFAULT_METADATA_WEIGHT = 0.5


class TrainingLeakageError(RuntimeError):
    """Raised when training would touch a frozen test row or its label."""


class FeatureSchemaMismatchError(RuntimeError):
    """Raised when a model is used with different feature definitions than it was
    trained with."""


# --------------------------------------------------------------------------- #
# scikit-learn transformers (module level so trained models can be pickled)
# --------------------------------------------------------------------------- #

class RowTextExtractor(BaseEstimator, TransformerMixin):
    """Rows -> model text. ``variant`` is ``"legacy"`` or ``"normalized"``."""

    def __init__(self, variant: str = "normalized") -> None:
        self.variant = variant

    def fit(self, X: Sequence[Mapping[str, Any]], y: Any = None) -> "RowTextExtractor":
        if self.variant not in {"legacy", "normalized"}:
            raise ValueError(f"Unknown text variant {self.variant!r}")
        self.is_fitted_ = True  # lets scikit-learn's check_is_fitted pass
        return self

    def transform(self, X: Sequence[Mapping[str, Any]]) -> list[str]:
        builder = legacy_model_text if self.variant == "legacy" else normalized_model_text
        return [builder(row) for row in X]


class RowMetadataExtractor(BaseEstimator, TransformerMixin):
    """Rows -> dense structured-feature matrix using the shared feature module."""

    def __init__(
        self,
        task: str | None = None,
        gmail_label_mode: str = "independent",
        reference_time: datetime | None = None,
    ) -> None:
        self.task = task
        self.gmail_label_mode = gmail_label_mode
        self.reference_time = reference_time

    def _context(self) -> FeatureContext:
        return FeatureContext(
            task=self.task,
            gmail_label_mode=self.gmail_label_mode,
            reference_time=self.reference_time,
        )

    def fit(self, X: Sequence[Mapping[str, Any]], y: Any = None) -> "RowMetadataExtractor":
        self._context()  # validates the configuration early
        self.is_fitted_ = True
        return self

    def transform(self, X: Sequence[Mapping[str, Any]]) -> "np.ndarray":
        matrix = feature_matrix(X, self._context())
        return np.asarray(matrix, dtype=float).reshape(len(matrix), len(FEATURE_NAMES))


class ScaleBy(BaseEstimator, TransformerMixin):
    """Multiply a feature block by a constant (balances it against TF-IDF)."""

    def __init__(self, weight: float = 1.0) -> None:
        self.weight = weight

    def fit(self, X: Any, y: Any = None) -> "ScaleBy":
        self.is_fitted_ = True  # stateless, but must look fitted as a last step
        return self

    def transform(self, X: Any) -> Any:
        return X * float(self.weight)


# --------------------------------------------------------------------------- #
# Pipeline construction
# --------------------------------------------------------------------------- #

def _tfidf() -> TfidfVectorizer:
    # Identical to the settings in scripts.evaluate_priority_engine.
    return TfidfVectorizer(ngram_range=(1, 2), min_df=1, max_features=30000, sublinear_tf=True)


def _classifier(seed: int) -> LogisticRegression:
    # Identical to the settings in scripts.evaluate_priority_engine
    # (class_weight="balanced" is the project's class-imbalance handling).
    return LogisticRegression(max_iter=2000, class_weight="balanced", random_state=seed)


def build_pipeline(
    *,
    variant: str = "text_plus_metadata",
    seed: int = 42,
    task: str | None = None,
    gmail_label_mode: str = "independent",
    reference_time: datetime | None = None,
    metadata_weight: float = DEFAULT_METADATA_WEIGHT,
) -> Pipeline:
    """Build an unfitted pipeline for ``variant`` (see module docstring)."""
    if variant not in VARIANTS:
        raise ValueError(f"Unknown variant {variant!r}; expected one of {VARIANTS}")

    if variant == "text_only_legacy":
        return Pipeline([
            ("rows_to_text", RowTextExtractor("legacy")),
            ("tfidf", _tfidf()),
            ("classifier", _classifier(seed)),
        ])
    if variant == "text_only_normalized":
        return Pipeline([
            ("rows_to_text", RowTextExtractor("normalized")),
            ("tfidf", _tfidf()),
            ("classifier", _classifier(seed)),
        ])

    features = FeatureUnion([
        (
            "text",
            Pipeline([
                ("rows_to_text", RowTextExtractor("normalized")),
                ("tfidf", _tfidf()),
            ]),
        ),
        (
            "metadata",
            Pipeline([
                (
                    "rows_to_features",
                    RowMetadataExtractor(task, gmail_label_mode, reference_time),
                ),
                ("scale", MaxAbsScaler()),
                ("weight", ScaleBy(metadata_weight)),
            ]),
        ),
    ])
    return Pipeline([("features", features), ("classifier", _classifier(seed))])


# --------------------------------------------------------------------------- #
# Leakage-safe training and inference
# --------------------------------------------------------------------------- #

def assert_training_excludes_frozen(
    training_keys: Iterable[str],
    frozen_test_ids: Iterable[str] | None,
) -> None:
    """Raise ``TrainingLeakageError`` if any training key is a frozen test ID."""
    if not frozen_test_ids:
        return
    overlap = set(training_keys) & set(frozen_test_ids)
    if overlap:
        raise TrainingLeakageError(
            f"{len(overlap)} training row(s) belong to the frozen test set. "
            "Training must use development rows only."
        )


@dataclass
class TrainedEmailModel:
    """A fitted pipeline plus the metadata needed to use it safely."""

    pipeline: Pipeline
    task: str | None
    variant: str
    classes: tuple[str, ...]
    fingerprint: str
    training_rows: int

    def _check_schema(self) -> None:
        current = schema_fingerprint()
        if current != self.fingerprint:
            raise FeatureSchemaMismatchError(
                "Feature definitions changed since this model was trained "
                f"({self.fingerprint} -> {current}). Retrain before predicting."
            )

    def predict(self, rows: Sequence[Mapping[str, Any]]) -> list[str]:
        self._check_schema()
        return [str(label) for label in self.pipeline.predict([model_input_view(r) for r in rows])]

    def predict_proba(self, rows: Sequence[Mapping[str, Any]]) -> list[dict[str, float]]:
        self._check_schema()
        probabilities = self.pipeline.predict_proba([model_input_view(r) for r in rows])
        return [
            {label: float(value) for label, value in zip(self.classes, row)}
            for row in probabilities
        ]


def train_model(
    rows: Sequence[Mapping[str, Any]],
    labels: Sequence[str],
    *,
    keys: Sequence[str],
    frozen_test_ids: Iterable[str] | None,
    variant: str = "text_plus_metadata",
    seed: int = 42,
    task: str | None = None,
    gmail_label_mode: str = "independent",
    reference_time: datetime | None = None,
    metadata_weight: float = DEFAULT_METADATA_WEIGHT,
) -> TrainedEmailModel:
    """Fit a model on development rows only.

    ``rows`` are reduced to the allow-listed input view before fitting, and the
    ground truth arrives separately as ``labels`` — so truth, adjudication, and
    prediction columns cannot reach the feature code. ``keys`` are the row
    identities; any key present in ``frozen_test_ids`` aborts training.
    """
    if not (len(rows) == len(labels) == len(keys)):
        raise ValueError("rows, labels, and keys must have equal lengths")
    assert_training_excludes_frozen(keys, frozen_test_ids)
    if len(set(labels)) < 2:
        raise ValueError("Training needs at least two distinct classes")

    pipeline = build_pipeline(
        variant=variant,
        seed=seed,
        task=task,
        gmail_label_mode=gmail_label_mode,
        reference_time=reference_time,
        metadata_weight=metadata_weight,
    )
    pipeline.fit([model_input_view(row) for row in rows], list(labels))
    return TrainedEmailModel(
        pipeline=pipeline,
        task=task,
        variant=variant,
        classes=tuple(str(c) for c in pipeline.classes_),
        fingerprint=schema_fingerprint(),
        training_rows=len(rows),
    )
