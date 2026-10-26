# Applying the FRIDAY Phase 2 overlay

This is an **overlay**, not a replacement copy of your project. It contains only the Phase 2 implementation files, tests, and docs. It intentionally excludes private email CSVs, raw email bodies, credentials, databases, `.env`, frozen split manifests, and generated reports.

## Safety first

1. Extract the ZIP to a temporary folder.
2. Back up the corresponding existing files in your `D:\FRIDAY` project before copying anything. Some of those files already have local changes.
3. Copy the `scripts`, `tests`, and `docs` folders from the extracted overlay into the existing project root, merging folders and replacing only the listed files.
4. Do not replace `data/email_labels.csv` or `data/priority_engine_split.json` from this ZIP; neither is included.
5. Do not perform Git operations as part of this step.

## Files included

- `scripts/evaluate_priority_engine.py`
- `scripts/evaluate_priority_engine_advanced.py`
- `scripts/evaluate_verified_labels.py`
- `scripts/priority_engine_splits.py`
- `scripts/auto_label_emails.py`
- `tests/test_priority_engine_evaluation.py`
- `tests/test_priority_engine_hardening.py`
- `docs/PHASE2_PRIORITY_ENGINE_COMPLETE_IMPLEMENTATION_PLAN.md`
- `docs/PHASE2_OVERLAY_INSTALL.md`
- `docs/PHASE2_IMPLEMENTATION_CHANGELOG.md`

## Verify the overlay

From the actual project root, activate the project's virtual environment and run:

```powershell
python -m pytest -q
```

Then inspect the dataset header and backup status before migrating:

```powershell
Test-Path .\data\email_labels.csv
Test-Path .\data\email_labels.csv.bak
```

Only run the migration after confirming the input and backup state:

```powershell
python -m scripts.migrate_email_label_schema --input data/email_labels.csv
```

Finally, run the evaluator:

```powershell
python -m scripts.evaluate_priority_engine --input data/email_labels.csv --output reports/priority_engine_evaluation.json --split-manifest data/priority_engine_split.json
```

Review the JSON/Markdown report for per-task status. `insufficient_verified_data` is a meaningful stop condition, not a failure to hide. Do not tune the holdout.
