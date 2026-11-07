from __future__ import annotations

import io
import json
import socket
import urllib.error

import pytest

from backend.services.ollama_classifier import (
    Classification,
    OllamaConfig,
    OllamaOutputError,
    OllamaTimeoutError,
    OllamaUnavailableError,
    classify_with_fallback,
    classify_with_ollama,
)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def config():
    return OllamaConfig(host="http://localhost:11434", model="synthetic-model")


def valid_payload(**updates):
    result = {
        "priority": "NEEDS_REPLY",
        "needs_response": "yes",
        "category": "JOB",
        "confidence": 0.91,
        "reason": "Synthetic evidence.",
    }
    result.update(updates)
    return {"response": json.dumps(result)}


def test_valid_response_returns_versioned_classification():
    result = classify_with_ollama(
        {"sender": "synthetic@example.test", "subject": "Synthetic", "body_text": "Please confirm."},
        config(),
        opener=lambda request, timeout: FakeResponse(valid_payload()),
    )
    assert isinstance(result, Classification)
    assert result.priority == "NEEDS_REPLY"
    assert result.needs_response == "yes"
    assert result.is_spam == "no"
    assert result.classifier_method == "ollama"
    assert result.classifier_version == "synthetic-model:friday-classifier-v1"


@pytest.mark.parametrize("updates", [
    {"priority": "UNKNOWN"},
    {"needs_response": "maybe"},
    {"category": "UNKNOWN"},
    {"confidence": True},
    {"confidence": float("nan")},
    {"confidence": 1.1},
    {"reason": []},
])
def test_invalid_model_fields_are_rejected(updates):
    with pytest.raises(OllamaOutputError):
        classify_with_ollama(
            {}, config(),
            opener=lambda request, timeout: FakeResponse(valid_payload(**updates)),
        )


def test_malformed_inner_json_is_rejected():
    with pytest.raises(OllamaOutputError, match="malformed"):
        classify_with_ollama(
            {}, config(),
            opener=lambda request, timeout: FakeResponse({"response": "{bad"}),
        )


def test_missing_response_envelope_is_rejected():
    with pytest.raises(OllamaOutputError):
        classify_with_ollama({}, config(), opener=lambda request, timeout: FakeResponse({}))


def test_timeout_is_a_controlled_failure():
    def timeout(request, timeout):
        raise TimeoutError()
    with pytest.raises(OllamaTimeoutError):
        classify_with_ollama({}, config(), opener=timeout)


def test_unavailable_service_is_a_controlled_failure():
    def unavailable(request, timeout):
        raise urllib.error.URLError("synthetic")
    with pytest.raises(OllamaUnavailableError):
        classify_with_ollama({}, config(), opener=unavailable)


def test_fallback_is_deterministic_and_does_not_expose_exception_text():
    def fail(row, cfg):
        raise OllamaUnavailableError("synthetic private detail")
    rules = lambda row: {
        "priority": "INFORMATIONAL",
        "needs_response": "no",
        "category": "OTHER",
        "confidence": 0.8,
        "classifier_version": "rules-test-v1",
    }
    result = classify_with_fallback({}, rules, config(), ollama_classifier=fail)
    assert result["classifier_method"] == "rules_fallback"
    assert result["fallback_reason"] == "OllamaUnavailableError"
    assert "synthetic private detail" not in repr(result)
    assert result["priority"] == "INFORMATIONAL"


@pytest.mark.parametrize("bad_config", [
    OllamaConfig(timeout_seconds=0),
    OllamaConfig(max_body_chars=-1),
    OllamaConfig(num_ctx=100),
    OllamaConfig(temperature=3),
    OllamaConfig(host="file:///tmp"),
])
def test_invalid_configuration_is_rejected(bad_config):
    with pytest.raises(ValueError):
        classify_with_ollama({}, bad_config)
