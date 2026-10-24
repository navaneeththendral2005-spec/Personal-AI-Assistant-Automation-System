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
