# FRIDAY advanced classifier evaluation

This is an additive evaluation tool. It does not change Gmail, production classification, or the existing reports.

## Run

```powershell
python -m scripts.evaluate_priority_engine_advanced --input data/email_labels.csv
```

Optional dependencies: `pip install -r requirements-ml.txt`.

## Method

- Uses only valid human-entered `true_priority`, `true_needs_response`, and `true_category` values as ground truth.
- Uses the existing private `data/priority_engine_split.json` manifest to keep the frozen test set separate.
- Performs stratified cross-validation on development rows only (up to five folds, limited by the smallest development class).
- Trains TF-IDF + Logistic Regression on development rows and evaluates it once on the frozen test IDs.
- Reports rules and stored selected predictions alongside the ML baseline.
- Adds percentile-bootstrap 95% intervals for holdout accuracy and macro-F1.
- Writes aggregate metrics only. No message text or identifiers are included in the report.

## Important limitations

- The frozen test set is small, so intervals can be wide and metrics unstable.
- If a manifest was already created, do not delete or recreate it to improve scores.
- The selected predictions in a prior `--no-llm` run are rules-based fallback predictions; do not describe them as an LLM/hybrid result.
- The script does not promote a model into production. Promotion requires a separate review and reproducible evidence.
