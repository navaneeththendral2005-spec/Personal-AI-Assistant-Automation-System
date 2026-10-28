# FRIDAY Project Audit and Remediation

> Consolidated from the three audit documents in the uploaded source snapshot. The source sections are preserved below so specific findings and caveats are not lost. Check claims and test counts against the current checkout before publishing.


---

## General audit results

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

---

## Remediation and evaluation gate

# FRIDAY audit remediation notes

This document records changes made to the source-review snapshot. It is not a claim that the model is accurate or that live Gmail/Ollama/provider behavior has been validated.

## Changes in this revision

- Fixed the `.gitignore` Python bytecode rule from the malformed `**pycache**/` pattern to `__pycache__/`. Added common SQLite sidecars, backup artifacts, credential-file patterns, and OS/editor files.
- The evaluation adapter preserves an explicit Gmail `SPAM` label as `SPAM`; it does not incorrectly equate the Promotions category with spam. Explicit response requests continue to map to `NEEDS_REPLY` when no stronger urgency label applies.
- Auto-labeling now stores direct Ollama outputs (`llm_priority`, `llm_needs_response`, `llm_category`, `llm_confidence`, `llm_reason`) separately from rules outputs and the selected provisional prediction.
- Added `llm_status` values that distinguish `disabled`, `success`, `cache_hit`, and `failed`. Intentional rules-only runs now use `label_source=rules_only` rather than incorrectly claiming a fallback.
- Preserved a bounded Ollama failure detail in the review reason so failed inference can be diagnosed without printing or storing a full traceback. Failures are always queued for human review.
- Verified-label evaluation now reports direct stored Ollama predictions separately from rules, selected predictions, and TF-IDF + Logistic Regression. Missing LLM predictions count as errors and reduce coverage.
- Added regression tests for the separated model outputs and the rules-only provenance status.
- Updated README workflow guidance and explicitly kept production on rules until evidence from a representative, human-verified holdout justifies a reviewed promotion.

## Evaluation and promotion gate

1. Use a representative, manually verified dataset with enough examples in each important class.
2. Keep a frozen test manifest private and never tune against it. Use development cross-validation for model selection.
3. Run auto-labeling and inspect `llm_status` and `label_source`; `rules_fallback` means inference failed, while `rules_only` means it was deliberately disabled.
4. Compare rules, stored Ollama, selected predictions, and TF-IDF + Logistic Regression on the same holdout. Treat small-sample results as exploratory.
5. Consider latency, failure rate, coverage, class-level recall (especially `NEEDS_REPLY` and `SPAM`), and confidence intervals—not accuracy alone.
6. Only after review, implement a separate production adapter/feature flag and test it independently. This patch intentionally does not route production email decisions to Ollama.

## Known limitations

- The source-only archive does not include the private email dataset, so dataset-specific metrics in earlier screenshots cannot be independently reproduced from this archive.
- A passing unit test suite does not prove Ollama is installed, reachable, or returns useful predictions in the user's environment.
- The rules classifier remains heuristic. Its output labels and thresholds need to be evaluated against human-verified labels.
- Historical evaluation scripts are retained to avoid breaking existing commands. Their roles are documented in the README rather than deleting potentially referenced entry points during an audit.
- `.gitignore` protects untracked files only; it does not untrack files already committed. Verify actual repository tracking/history separately.

---

## Project audit and next steps

# FRIDAY project audit and next steps

This document describes source-level work and testable behavior; it is not a claim that every live Gmail, database, OAuth, dashboard, or external-provider scenario was exercised.

## Current components in the reviewed source snapshot

- FastAPI backend, SQLAlchemy models, Alembic migrations, and dashboard assets.
- Gmail OAuth/read operations, message extraction, paginated synchronization, and additive upserts.
- Email classification and intelligence extraction for category, priority, response need, deadlines, actions, and career signals.
- Local assistant queries over stored data, task/reminder services, and career opportunity tracking.
- CSV export and resumable human labeling, rules-based predictions, local Ollama classification, and selective review.

## Work included in this hardening package

1. Corrected deadline-query semantics and made current time injectable for deterministic tests.
2. Added local persistent Ollama prediction caching keyed by model/prompt/input fingerprint.
3. Added cache-aware progress, ETA, end-of-run summary, and actionable bounded error diagnostics.
4. Added verified-label holdout comparison for priority, needs-response, and category.
5. Added tests for caching and content-based invalidation, and deterministic deadline tests.
6. Documented privacy boundaries, limitations, evaluation steps, and the production-integration gate.

## Known limitations / not falsely claimed as complete

- The uploaded dataset snapshot contains private email content and is excluded from this source-only ZIP.
- The reported local run processed 300 emails; 296 Ollama calls succeeded and 4 fell back to rules. That is inference success, not accuracy.
- Only one human-verified value was present per `true_*` field in the reported dataset snapshot. This is insufficient for a meaningful evaluation.
- The evaluation script marks small samples as exploratory and refuses to treat predictions as truth. It does not fabricate metrics for unverified rows.
- Production integration is intentionally gated until a representative verified dataset supports a model choice. No migration is made without that evidence.
- The reviewed archive's prior deadline test used a fixed date but expected no overdue result even though the fixture's interview deadline was earlier. The tests now use an explicit `now` and assert the actual intended upcoming/overdue behavior.
- API authentication, production deployment hardening, and complete external-provider privacy review remain separate work items before public deployment.

## Recommended operational sequence

1. Run `python -m pytest -q`.
2. Keep private data local and confirm Git status does not include exports, backups, review queue, database, tokens, or cache.
3. Run auto-labeling; inspect the summary and fallback diagnostics.
4. Human-review the highest-risk disagreements and fill `true_*` fields manually.
5. Continue labeling a representative sample; do not use auto-predictions as ground truth.
6. Run `python -m scripts.evaluate_verified_labels --input data/email_labels.csv` after meaningful labels have been collected.
7. Inspect class-level errors and compare the rules baseline with the model prediction on the same holdout.
8. Only after the evidence supports a choice, plan production integration and schema/version tracking as a separate reviewed change.
