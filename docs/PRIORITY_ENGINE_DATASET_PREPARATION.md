# FRIDAY Priority Engine: prepare an evaluation dataset

This is an additive, offline data-preparation step. It does not modify the production classifier, Gmail, or the original CSV files.

## Command

Run from the FRIDAY project root in the project's virtual environment:

```powershell
python -m scripts.prepare_priority_engine_dataset
```

Defaults:

- Input: `data/email_labels.csv`
- Final adjudications: `data/label_disagreement_adjudication_final.csv`
- Output: `data/email_labels_evaluation.csv`
- Frozen split manifest: `data/priority_engine_split.json`

Use `--input`, `--adjudication`, `--output`, and `--split-manifest` to select alternate paths. The output path must not already exist; choose a new filename instead of overwriting a prior artifact.

## Merge and ground-truth policy

1. Source and adjudication records are matched only by the project's established `email_id` identity. Missing or duplicate IDs and adjudication IDs not present in the source dataset stop the run.
2. Final adjudication fields (`final_true_*`) are normalized and copied into the corresponding `true_*` columns for matched emails. These final human decisions take precedence over earlier `true_*` values.
3. For non-adjudicated emails, existing `true_*` values are preserved. This script cannot independently prove the human provenance of those existing values; confirm that they satisfy the project's human-verification policy before using them as ground truth.
4. `rules_*`, `predicted_*`, and `llm_*` values are preserved separately and are never copied into ground truth.
5. If final spam is `yes`, priority must be blank because priority is not applicable to spam. Spam is evaluated in its own task.
6. The output is a separate private CSV under `data/`, which is git-ignored in the project configuration.

## Frozen holdout policy

If the manifest exists, the script checks its schema, task schemas, frozen row membership, and task-specific ground truth against the prepared dataset. An incompatibility stops the run before output is written. The script never creates, edits, repairs, or regenerates the manifest. If the manifest is missing, the script warns that compatibility could not be checked and leaves the manifest absent.

After a successful preparation, run the evaluator explicitly against the prepared file, using the same frozen manifest:

```powershell
python -m scripts.evaluate_priority_engine --input data/email_labels_evaluation.csv --split-manifest data/priority_engine_split.json --output reports/priority_engine_evaluation_prepared.json
```

Do not tune a model against the frozen test results. Keep the source data, adjudication CSV, prepared dataset, and manifest private.

## Tests

```powershell
python -m pytest tests/test_prepare_priority_engine_dataset.py -q
```

The tests cover precedence, prediction preservation, spam/priority constraints, duplicate and unknown IDs, schema errors, no-overwrite behavior, privacy-safe summaries, and fail-closed frozen-manifest checks.
