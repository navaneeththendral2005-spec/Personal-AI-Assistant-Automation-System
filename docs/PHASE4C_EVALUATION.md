# Phase 4C — Controlled classifier comparison

Run from the repository root after verifying that `data/priority_engine_split.json`
is the existing frozen manifest:

```powershell
python -m pytest -q tests/test_phase4c_evaluate_hybrid.py
python -m pytest -q
python -m scripts.phase4c_evaluate_hybrid --input data/email_labels_evaluation.csv --manifest data/priority_engine_split.json --output reports/phase4c_evaluation.json --threshold 70
```

The evaluator compares three independent arms on task-specific frozen holdouts:

- **Rules-only:** deterministic existing classifier; no Ollama calls.
- **Ollama-only:** one local model inference per unique eligible email, regardless of
  rules confidence. Model failures remain invalid/missing predictions and are counted
  as failures; they are not silently replaced by rules.
- **Hybrid:** one routing decision per unique eligible email. Rules predictions are
  used when confidence meets the threshold; otherwise Ollama is attempted and a
  controlled adapter failure falls back to rules.

If an email is present in both task-specific holdouts, each strategy predicts it once
and reuses that prediction for both tasks. Invocation counts therefore represent
actual per-email strategy calls, not task-row accounting.

For each task and strategy, the report includes accuracy, per-class precision,
recall and F1, macro/weighted F1, coverage, and a confusion matrix. Routing
statistics include model invocation count/rate, hybrid fallback count/rate, model
failure counts for Ollama-only, and mean/p95 successful inference latency.

The evaluator reads only human `true_category` and `true_needs_response` labels
whose existing task-specific frozen manifest contains the row key. It refuses to
create or repair a manifest, never writes predictions into the input CSV, and emits
aggregate-only JSON/Markdown reports without email identifiers or message content.

Priority is deliberately excluded: the current rules and Ollama priority taxonomies
are not semantically equivalent. Category results must be interpreted against the
label-guide version used for the verified dataset; in particular, PERSONAL is
present in this evaluator's legacy category schema and must not be assumed valid
under a newer guide that merges it into OTHER.

Use validation/development data—not the frozen test report—to choose thresholds.
Ollama runs locally; no remote inference is configured.
