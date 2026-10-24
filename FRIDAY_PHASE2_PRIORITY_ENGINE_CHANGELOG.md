# Phase 2 Priority Engine hardening package

## Included changes

- Added a queue-aware labeling workflow (`--queue`, `--limit`) that uses provisional predictions as suggestions only and continues to save atomically.
- Added a reproducible priority-engine evaluator for priority, response requirement, and category.
- The evaluator uses only valid, non-empty `true_*` human labels; trains TF-IDF + Logistic Regression only on a stratified training split; reports rules, stored predictions, and ML metrics on the holdout; and avoids writing raw email content or message IDs.
- Insufficient verified data is reported explicitly instead of manufacturing accuracy estimates.
- Added classifier method/version provenance to `EmailAnalysis`, its API schema/payload, and a forward/down migration.
- Production remains on the existing rules classifier (`rules`, `friday-rules-v1`) until verified-label evidence supports a promotion.
- Added focused tests and an operational guide.

## Verification performed in the isolated uploaded project snapshot

- `python -m pytest -q`: **95 passed**
- `python -m alembic -c alembic.ini heads`: `friday_classifier_meta_07 (head)`
- Ran the evaluator against the included dataset: each task reported `insufficient_verified_data` because only one ground-truth label exists per task. No accuracy was claimed.

The archive is an overlay: extract its contents into the root of the existing FRIDAY project. It intentionally excludes private email CSVs, `.env`, credentials, databases, and generated reports.
