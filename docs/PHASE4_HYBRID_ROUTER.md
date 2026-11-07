# FRIDAY Phase 4B — Evaluation and routing policy

## Scope

This patch strengthens the isolated rules-first router. It does not connect the
router to Gmail, database persistence, automatic labeling, or email actions.
The existing rules result remains authoritative. Ollama output is advisory and
kept in a separate `model_suggestion` field.

## Configuration

Environment variables:

- `FRIDAY_HYBRID_ENABLED=true` enables hybrid routing (`false` makes it rules-only).
- `FRIDAY_HYBRID_CONFIDENCE_THRESHOLD=70` sets the rules confidence threshold
  on a 0–100 scale. Values at or above the threshold skip Ollama; lower,
  missing, or invalid rule confidence defers to Ollama.

The threshold is configurable, not empirically calibrated. The existing
`confidence_score` is a heuristic score from the rules classifier; do not
interpret it as a statistically calibrated probability. Keep the default at
70 until measured against a representative, human-labeled validation set.

## Result contract

`HybridResult.as_dict()` returns:

- `rules_result`: unchanged rules classifier output.
- `model_suggestion`: validated Ollama output or `null`.
- `routing_metadata`: route, rules confidence, configured threshold, controlled
  fallback reason, and (when available) model confidence/version/latency.

Routing metadata intentionally excludes sender, subject, snippet, body, and
exception messages. Do not log the full result object because model reason and
rules explanation can contain email-derived text.

## Failure policy

Controlled `OllamaError` failures return the unchanged rules result with
`route=rules_fallback`. Unexpected programming errors are not swallowed; they
remain visible to tests and callers. No automatic retry is performed.

## Evaluation plan

Before changing the threshold or enabling this router in production, run it on a
representative validation set that is separate from training and frozen test
records. Compare rules-only and hybrid suggestions against human labels using
per-class precision, recall, macro-F1, coverage, abstention/fallback rate, and
latency. Report results by task (category, priority, needs-response, spam), not
only one aggregate score. Do not tune on the frozen test set.

The current rules confidence score is heuristic. This patch provides a routing
control, not evidence that 70 is optimal or that model suggestions are more
accurate. Human labels remain the source of truth.
