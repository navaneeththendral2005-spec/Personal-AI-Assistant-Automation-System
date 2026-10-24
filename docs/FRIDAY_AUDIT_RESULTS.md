# FRIDAY source audit — 2026-10-24

## Snapshot and verification

The uploaded source archive contains backend services and routers, a browser dashboard, Alembic migrations, Gmail sync and body extraction, email/career intelligence, labeling and evaluation scripts, documentation, and tests.

The full test suite was run against an extracted copy of the archive in an isolated temporary directory: **100 passed**. This is a clean test run of the uploaded snapshot; it does not prove live Gmail, PostgreSQL migration, or Ollama integration works in the user's environment.

## Findings

1. `scripts/auto_label_emails.py` supports local Ollama inference, rules fallback, a local SQLite cache, provisional predictions, and a review queue. The shown successful 300-email run used `--no-llm`, so it evaluated only the rules path and did not test Ollama.
2. `reports/verified_label_evaluation.json` contains low holdout scores for the rules classifier on the current frozen set (priority accuracy 0.30 / macro-F1 0.192; needs-response accuracy 0.65 / macro-F1 0.394). These are snapshot-specific estimates from a small test set, not broad performance guarantees.
3. The current evaluation reports do not include development cross-validation or bootstrap confidence intervals. An additive evaluator is included in this patch.
4. The `data/` directory contains private email exports, labels, backups, checkpoint files, a review queue, and a manifest with private email IDs. Keep all of these out of Git and public archives.
5. `classifier_method` and `classifier_version` fields are present in the ORM model and a migration exists. The test suite passing does not verify the migration against the user's live database; inspect `alembic heads` and `alembic current` before applying database changes.
6. Existing production analysis remains rules-based by default. Do not claim an LLM/hybrid classifier is integrated or validated just because the optional auto-labeling script exists.
7. The supplied test suite covers many core paths, but tests cannot substitute for live Gmail OAuth/sync, real Ollama availability, database migration, or UI smoke tests.

## Safe next steps

- Preserve `data/email_labels.csv` and `data/email_labels_checkpoint_81.csv`; keep private data local.
- Run the new advanced evaluation command and inspect aggregate metrics.
- Only test Ollama after confirming the local server and model are installed; use synthetic test messages first.
- Do not tune against the frozen test set. Use development-only CV for comparisons.
- Before deploying schema changes, verify the Alembic revision graph and take a database backup.
