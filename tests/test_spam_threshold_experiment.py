from __future__ import annotations

from scripts.spam_threshold_experiment import (
    evaluate_thresholds,
)


def test_evaluate_thresholds_compares_precision_and_recall():
    y_true = ["yes", "yes", "no", "no"]

    probabilities = [0.90, 0.40, 0.60, 0.10]

    report = evaluate_thresholds(
        y_true,
        probabilities,
        thresholds=[0.30, 0.50, 0.70],
    )

    results = report["threshold_results"]

    assert len(results) == 3

    low = results[0]
    medium = results[1]
    high = results[2]

    assert low["threshold"] == 0.30
    assert low["spam_recall"] == 1.0

    assert medium["threshold"] == 0.50
    assert medium["spam_recall"] == 0.5

    assert high["threshold"] == 0.70
    assert high["spam_recall"] == 0.5

    assert all(
        0.0 <= result["spam_precision"] <= 1.0
        for result in results
    )

    assert all(
        0.0 <= result["false_positive_rate"] <= 1.0
        for result in results
    )


def test_evaluate_thresholds_rejects_mismatched_inputs():
    import pytest

    with pytest.raises(ValueError, match="equal lengths"):
        evaluate_thresholds(
            ["yes", "no"],
            [0.8],
            thresholds=[0.5],
        )


def test_evaluate_thresholds_rejects_invalid_thresholds():
    import pytest

    with pytest.raises(ValueError, match="between 0 and 1"):
        evaluate_thresholds(
            ["yes", "no"],
            [0.8, 0.2],
            thresholds=[1.5],
        )