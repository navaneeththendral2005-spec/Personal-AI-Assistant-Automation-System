# FRIDAY Priority Engine — Phases A–E Rollout

This rollout was prepared against the uploaded FRIDAY source snapshot. It preserves the existing production classifier and the frozen holdout manifest. It adds a development-only cross-validation command and regression tests so classifier changes can be evaluated without tuning against the holdout.

## Scope and safety decisions

- The production email classifier was **not silently replaced or retuned**. Aggregate holdout results are too small to justify changing production behavior by themselves.
- `data/priority_engine_split.json` remains the source of truth for the frozen holdout. The new development evaluator is read-only with respect to that manifest and fails if it is missing, malformed, or source row keys are duplicated.
- The full project snapshot includes local email-labeling CSVs and a private manifest from the supplied archive. Keep the ZIP, `data/`, and manifest private. Do not commit or share them.
- Reports contain aggregate metrics only. Do not add email text, sender addresses, or raw IDs to reports or screenshots.

## Phase A — Establish a safe baseline

The current frozen-holdout baseline from `reports/priority_engine_evaluation.json` is:

| Task | Rules macro-F1 | Selected macro-F1 | Ollama coverage | Word TF-IDF macro-F1 |
|---|---:|---:|---:|---:|
| Priority | 0.3556 | 0.3556 | 36.36% | 0.1333 |
| Spam | 0.3548 | 0.3548 | 20% | 0.4950 |
| Needs response | 0.3940 | 0.5312 | 10% | 0.7151 |
| Category | 0.1292 | 0.1407 | 30% | 0.1976 |

The holdout sizes are only 11 priority examples and 20 examples for each other task. Treat these scores as a baseline, not as precise estimates of future performance.

Before changing classifier behavior:

1. Keep a private backup of `data/priority_engine_split.json`.
2. Keep the current aggregate evaluation report.
3. Do not delete, regenerate, or edit frozen test IDs to improve scores.
4. Do not use the holdout rows to write new rules or tune model hyperparameters.

## Phase B — Improve rules using development data

The new development evaluator first excludes the frozen test IDs and then measures the current rules on the remaining human-verified rows with stratified cross-validation. Use its class-level confusion matrices and per-class recall to identify development errors.

Suggested review priorities from the current baseline:

- **Priority:** the holdout rules missed 3 of 4 urgent examples and both `NEEDS_REPLY` examples. Use development-only examples to distinguish explicit urgency from a routine request for a reply.
- **Spam:** the current rules predicted `no` for every holdout example. Add spam evidence only when supported by the message and avoid treating Gmail Promotions or legitimate newsletters as spam.
- **Needs response:** the rules missed all 7 positive holdout examples. Review direct requests, questions that require a response, confirmation requests, and no-reply/automated-message exceptions.
- **Category:** all six categories need work, particularly classes with few verified examples. Improve category boundaries and collect more human-reviewed examples before relying on class-level conclusions.

These are review targets, not instructions to copy individual holdout examples into rules. Make one change at a time, add a synthetic regression test for it, and compare development cross-validation results before deciding whether to keep it. This snapshot intentionally leaves production rules unchanged until those candidate changes have been reviewed and tested.

## Phase C — Compare candidate classifiers

Run the development-only comparison:

```powershell
python -m scripts.evaluate_priority_engine_development
```

The script compares:
- Existing production rules.
- Word n-gram TF-IDF + Logistic Regression.
- Character n-gram TF-IDF + Logistic Regression.

It chooses a stratified fold count based on the smallest represented development class. If there are not enough examples, it reports insufficient data instead of pretending a model was evaluated. It writes only aggregate metrics to `reports/priority_engine_development_cv.json`.

In the supplied snapshot, the first development-only run produced these macro-F1 scores:

| Task | Rules | Word TF-IDF | Character TF-IDF |
|---|---:|---:|---:|
| Priority | 0.2389 | 0.2626 | 0.2121 |
| Spam | 0.3646 | 0.6678 | 0.6678 |
| Needs response | 0.4019 | 0.5908 | 0.5643 |
| Category | 0.2734 | 0.1864 | 0.1941 |

These cross-validation values are **not directly comparable** to the frozen-holdout scores above: they use development rows and out-of-fold predictions. They suggest that TF-IDF deserves further investigation for spam and needs-response classification, while rules remain competitive for category. They do not authorize automatic production promotion.

## Phase D — Improve Ollama output reliability

The existing auto-labeling workflow validates model JSON, rejects unsupported labels, records status/provenance, caches successful outputs locally, and sends failures for human review. Keep those fail-closed behaviors.

Use these checks when diagnosing low coverage:

1. Run the existing auto-labeler on a small, synthetic or approved local sample before a large batch.
2. Inspect aggregate `llm_status` counts and bounded failure reasons; do not dump email bodies into logs.
3. Verify that valid output contains exactly the supported label values and confidence in `[0, 1]`.
4. Test empty output, malformed JSON, missing fields, unsupported labels, timeout, and Ollama-unavailable cases.
5. Do not convert a failed or missing prediction into a guessed label. It must remain missing and count against coverage.

## Phase E — Re-evaluate against the frozen holdout

After changes have passed regression tests and development cross-validation:

```powershell
python -m pytest -q
python -m scripts.evaluate_priority_engine --input data/email_labels.csv --output reports/priority_engine_evaluation.json --split-manifest data/priority_engine_split.json
```

Compare the new report with the saved baseline. Do not repeatedly tune against the frozen test set. If results are inconclusive, collect more human-verified examples and create a **new, separately versioned evaluation protocol** only with an explicit decision; do not silently rewrite the current manifest.

## Test and inspection record

- The supplied source archive was reconstructed into its intended `backend/`, `scripts/`, `tests/`, `migrations/`, `dashboard/`, `data/`, `reports/`, and `docs/` layout because its ZIP entries were flattened.
- Before adding the development evaluator, the reconstructed source snapshot passed **159 tests**.
- The new development-evaluator tests cover frozen-ID exclusion, manifest immutability, privacy of the report, missing-manifest behavior, and duplicate-row-key rejection.
- Live Gmail/OAuth, PostgreSQL migrations against a real database, and an external/local Ollama server were not exercised by this offline test run.
