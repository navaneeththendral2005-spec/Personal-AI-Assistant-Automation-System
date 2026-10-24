# FRIDAY Phase 2 — Priority Engine evaluation hardening

## What changed

- The rules adapter now passes an attribute-bearing message object to `classify_email()`. The production classifier uses `getattr()`, so passing a plain dictionary silently discarded sender, subject, snippet, body, and labels.
- Ollama inference failures always put the email into the human review queue, even when the fallback rules confidence is high.
- Evaluation counts missing/invalid predictions as errors. Reports include `prediction_coverage` and `invalid_or_missing_predictions`; these cases are not silently filtered from the metric denominator.
- `evaluate_verified_labels.py` uses the same case normalization and metrics behavior as the main evaluator.
- Both evaluation entry points use a persistent, private, task-specific split manifest. Once a task's test IDs are created, newly labeled IDs are development-only and do not silently enter the frozen test set.
- Tests use synthetic messages and do not call Gmail or Ollama.

## Before running

1. Keep `data/email_labels.csv`, the checkpoint CSV, and all review queues local and out of Git.
2. The split manifest (`data/priority_engine_split.json`) contains email identifiers. Keep it private and git-ignored; do not commit or share it.
3. Do not delete or regenerate the manifest casually. Deleting it changes the test set and makes before/after comparisons invalid. If you intentionally want a new experiment, preserve the old manifest under a clearly named local backup and document the new evaluation version.
4. This change does not alter any `true_*` labels. Predictions and reports should be regenerated after installation.

## Install overlay

Copy the files from the supplied overlay ZIP into the project root, preserving the `scripts/`, `tests/`, and `docs/` directories. Allow the listed files to be replaced. No database migration is required.

## Verification (PowerShell)

```powershell
python -m pytest tests/test_priority_engine_evaluation.py tests/test_priority_engine_hardening.py tests/test_auto_label_emails.py -q
python -m pytest -q
```

## Regenerate provisional predictions

First create a backup outside Git or use the existing local checkpoint. Then run the local rules-only path (no network/model request):

```powershell
python -m scripts.auto_label_emails --input data/email_labels.csv --no-llm --review-sample-rate 0.10
```

This updates provisional prediction columns and `data/email_review_queue.csv`. It must not overwrite `true_*` columns. Inspect the diff/CSV counts before proceeding.

## Evaluate

```powershell
python -m scripts.evaluate_priority_engine --input data/email_labels.csv
python -m scripts.evaluate_verified_labels --input data/email_labels.csv
```

The first run creates `data/priority_engine_split.json`. Reports go under `reports/` and contain aggregate metrics only. Check each task's `status`, `frozen_test_rows`, `prediction_coverage`, and `invalid_or_missing_predictions`. Treat small samples as exploratory; never tune model thresholds on the frozen test set.

## Important limitations

- A frozen split improves reproducibility but does not make a small dataset statistically strong.
- Different tasks have task-specific test assignments because priority, response need, and category have different class distributions.
- A test assignment is stratified when the data permit it; tiny or rare classes can force a deterministic fallback and should be noted.
- The rules model remains heuristic and is not automatically promoted to production ML. Any classifier switch requires a separate, reviewed change.
