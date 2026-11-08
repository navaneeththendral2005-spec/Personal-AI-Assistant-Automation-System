from dataclasses import dataclass

import pytest

from backend.services.hybrid_router import HybridConfig, classify_hybrid
from backend.services.ollama_classifier import (
    Classification,
    OllamaOutputError,
    OllamaUnavailableError,
)


@dataclass
class Email:
    sender: str = "sender@example.test"
    subject: str = "Synthetic subject"
    snippet: str = "Synthetic snippet"
    body_text: str = "Synthetic body"


def rules(confidence=90):
    return {"category": "OTHER", "priority": "NORMAL", "priority_confidence_score": confidence, "confidence_score": confidence, "classification_reason": "synthetic"}


def suggestion():
    return Classification("no", "NEEDS_REPLY", "yes", "PERSONAL", 0.82, "synthetic", "ollama", "test-model:test", 12.0)


def test_confident_rules_skip_ollama():
    calls = []
    result = classify_hybrid(
        Email(),
        config=HybridConfig(enabled=True, confidence_threshold=70),
        rules_classifier=lambda _: rules(90),
        ollama_classifier=lambda *a: pytest.fail("Ollama must not run for confident priority rules"),
    )
    assert result.route == "rules_confident" and result.model_suggestion is None and calls == []



def test_priority_confidence_controls_routing_not_aggregate_confidence():
    # Aggregate confidence can be high while the priority decision is uncertain.
    result = classify_hybrid(
        Email(),
        rules_classifier=lambda _: {
            **rules(90),
            "priority_confidence_score": 40,
        },
        ollama_classifier=lambda *a: suggestion(),
    )
    assert result.route == "rules_plus_ollama_advisory"
    assert result.rules_confidence == 40



def test_low_confidence_gets_advisory_without_overwriting_rules():
    result = classify_hybrid(Email(), rules_classifier=lambda _: rules(40), ollama_classifier=lambda *a: suggestion())
    assert result.route == "rules_plus_ollama_advisory"
    assert result.rules_result["priority"] == "NORMAL"
    assert result.model_suggestion["priority"] == "NEEDS_REPLY"


def test_configured_threshold_controls_routing_boundary():
    calls = []
    classifier = lambda *a: calls.append(a) or suggestion()
    assert classify_hybrid(Email(), config=HybridConfig(confidence_threshold=65), rules_classifier=lambda _: rules(65), ollama_classifier=classifier).route == "rules_confident"
    assert classify_hybrid(Email(), config=HybridConfig(confidence_threshold=66), rules_classifier=lambda _: rules(65), ollama_classifier=classifier).route == "rules_plus_ollama_advisory"
    assert len(calls) == 1


@pytest.mark.parametrize("error", [OllamaUnavailableError, OllamaOutputError])
def test_controlled_failure_falls_back_to_rules(error):
    def fail(*args):
        raise error("synthetic private detail")
    result = classify_hybrid(Email(), rules_classifier=lambda _: rules(20), ollama_classifier=fail)
    assert result.route == "rules_fallback"
    assert result.fallback_reason == error.__name__
    assert result.rules_result["category"] == "OTHER"
    assert result.model_suggestion is None
    assert "synthetic private detail" not in repr(result.as_dict())


def test_disabled_mode_never_calls_ollama():
    result = classify_hybrid(Email(), config=HybridConfig(enabled=False), rules_classifier=lambda _: rules(1), ollama_classifier=lambda *a: pytest.fail("called"))
    assert result.route == "rules_only_disabled"


@pytest.mark.parametrize("value", [None, "unknown", True, float("nan"), float("inf"), -1, 101])
def test_invalid_or_missing_rules_confidence_defers_to_ollama(value):
    result = classify_hybrid(Email(), rules_classifier=lambda _: rules(value), ollama_classifier=lambda *a: suggestion())
    assert result.route == "rules_plus_ollama_advisory"
    assert result.rules_confidence is None


@pytest.mark.parametrize("threshold", [True, float("nan"), float("inf"), -1, 101, "70"])
def test_invalid_threshold_rejected(threshold):
    with pytest.raises(ValueError):
        classify_hybrid(Email(), config=HybridConfig(confidence_threshold=threshold))


def test_environment_configuration(monkeypatch):
    monkeypatch.setenv("FRIDAY_HYBRID_ENABLED", "false")
    monkeypatch.setenv("FRIDAY_HYBRID_CONFIDENCE_THRESHOLD", "62.5")
    config = HybridConfig.from_env()
    assert config.enabled is False
    assert config.confidence_threshold == 62.5


def test_invalid_environment_boolean_rejected(monkeypatch):
    monkeypatch.setenv("FRIDAY_HYBRID_ENABLED", "sometimes")
    with pytest.raises(ValueError):
        HybridConfig.from_env()


def test_metadata_tracks_decision_without_copying_email_content():
    result = classify_hybrid(Email(), config=HybridConfig(confidence_threshold=80), rules_classifier=lambda _: rules(30), ollama_classifier=lambda *a: suggestion()).as_dict()
    metadata = result["routing_metadata"]
    assert metadata["route"] == "rules_plus_ollama_advisory"
    assert metadata["rules_confidence"] == 30
    assert metadata["confidence_threshold"] == 80
    assert metadata["model_confidence"] == 0.82
    assert metadata["model_version"] == "test-model:test"
    assert "subject" not in metadata and "body_text" not in metadata


def test_unexpected_programming_error_is_not_silently_swallowed():
    def broken(*args):
        raise RuntimeError("bug")
    with pytest.raises(RuntimeError, match="bug"):
        classify_hybrid(Email(), rules_classifier=lambda _: rules(1), ollama_classifier=broken)
