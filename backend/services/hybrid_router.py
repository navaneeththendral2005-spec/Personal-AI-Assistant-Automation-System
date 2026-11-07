from __future__ import annotations

import math
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from backend.services.email_classifier import classify_email
from backend.services.ollama_classifier import (
    Classification,
    OllamaConfig,
    OllamaError,
    classify_with_ollama,
)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean (true/false)")


@dataclass(frozen=True)
class HybridConfig:
    enabled: bool = True
    confidence_threshold: float = 70.0

    @classmethod
    def from_env(cls) -> "HybridConfig":
        raw_threshold = os.getenv("FRIDAY_HYBRID_CONFIDENCE_THRESHOLD", "70")
        try:
            threshold = float(raw_threshold)
        except (TypeError, ValueError):
            raise ValueError("FRIDAY_HYBRID_CONFIDENCE_THRESHOLD must be numeric") from None
        config = cls(
            enabled=_env_bool("FRIDAY_HYBRID_ENABLED", True),
            confidence_threshold=threshold,
        )
        config.validate()
        return config

    def validate(self) -> None:
        if isinstance(self.confidence_threshold, bool) or not isinstance(self.confidence_threshold, (int, float)):
            raise ValueError("confidence_threshold must be numeric")
        if not math.isfinite(float(self.confidence_threshold)) or not 0 <= self.confidence_threshold <= 100:
            raise ValueError("confidence_threshold must be between 0 and 100")
        if not isinstance(self.enabled, bool):
            raise ValueError("enabled must be a boolean")


@dataclass(frozen=True)
class HybridResult:
    """Result plus privacy-safe routing metadata; no persistence is performed."""

    rules_result: dict[str, Any]
    model_suggestion: dict[str, Any] | None
    route: str
    fallback_reason: str | None
    rules_confidence: float | None
    confidence_threshold: float

    def as_dict(self) -> dict[str, Any]:
        suggestion = dict(self.model_suggestion) if self.model_suggestion is not None else None
        metadata: dict[str, Any] = {
            "route": self.route,
            "rules_confidence": self.rules_confidence,
            "confidence_threshold": self.confidence_threshold,
            "fallback_reason": self.fallback_reason,
        }
        if suggestion is not None:
            metadata["model_confidence"] = suggestion.get("confidence")
            metadata["model_version"] = suggestion.get("classifier_version")
            metadata["model_latency_ms"] = suggestion.get("latency_ms")
        return {
            "rules_result": dict(self.rules_result),
            "model_suggestion": suggestion,
            "routing_metadata": metadata,
        }


def _email_mapping(email: Any) -> dict[str, str]:
    """Build a bounded-field mapping; the Ollama adapter applies its own limits."""
    return {
        key: str(getattr(email, key, None) or "")
        for key in ("sender", "subject", "snippet", "body_text")
    }


def _rules_confidence(rules: Mapping[str, Any]) -> float | None:
    value = rules.get("confidence_score")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    if not math.isfinite(numeric) or not 0 <= numeric <= 100:
        return None
    return numeric


def classify_hybrid(
    email: Any,
    *,
    config: HybridConfig | None = None,
    ollama_config: OllamaConfig | None = None,
    rules_classifier: Callable[[Any], Mapping[str, Any]] = classify_email,
    ollama_classifier: Callable[..., Classification] = classify_with_ollama,
) -> HybridResult:
    """Run rules first; defer to local Ollama only below the confidence threshold.

    Missing/invalid rule confidence is treated as insufficient confidence. Only
    controlled Ollama adapter failures trigger fallback; programming errors are
    deliberately not hidden.
    """
    config = config or HybridConfig.from_env()
    config.validate()
    rules = dict(rules_classifier(email))
    confidence = _rules_confidence(rules)

    if not config.enabled:
        return HybridResult(rules, None, "rules_only_disabled", None, confidence, float(config.confidence_threshold))
    if confidence is not None and confidence >= config.confidence_threshold:
        return HybridResult(rules, None, "rules_confident", None, confidence, float(config.confidence_threshold))

    try:
        model_result = ollama_classifier(_email_mapping(email), ollama_config)
        if not isinstance(model_result, Classification):
            raise TypeError("ollama_classifier must return Classification")
        suggestion = model_result.as_dict()
    except OllamaError as exc:
        return HybridResult(rules, None, "rules_fallback", type(exc).__name__, confidence, float(config.confidence_threshold))

    return HybridResult(rules, suggestion, "rules_plus_ollama_advisory", None, confidence, float(config.confidence_threshold))
