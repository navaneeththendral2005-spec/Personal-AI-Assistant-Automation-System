# Phase 2 implementation overlay — change log

## Implemented in this overlay

- Added a dedicated `is_spam` evaluation task based on human-confirmed `true_is_spam`.
- Defined priority evaluation around `URGENT`, `NEEDS_REPLY`, and `INFORMATIONAL`; legacy `true_priority=SPAM` annotations remain unchanged and are excluded from priority metrics.
- Preserved the old four-label priority split schema when resolving frozen priority IDs, so the taxonomy clarification does not force a holdout reset.
- Added backward-compatible prediction mapping for older CSVs without separate spam prediction columns.
- Added separate `predicted_is_spam`, `rules_is_spam`, and `llm_is_spam` columns in the auto-labeling flow.
- Added a clear `insufficient_training_classes` evaluation status when the frozen split leaves only one class in development.
- Updated the advanced and verified-label evaluators to use the same spam/priority task contract.
- Added regression tests for separate spam evaluation, legacy-label exclusion, and split/class edge cases.

## Verification

The source archive was reconstructed into the expected `backend/`, `scripts/`, `tests/`, `data/`, `docs/`, and `reports/` layout in an isolated temporary workspace. The full test suite completed with **154 passed** in the reconstructed isolated workspace after the final regression assertions were added. The archive itself was flattened, so the reconstructed layout is inferred from module imports and project conventions. The ZIP contains only overlay files, not the private data or local runtime state.

## Still requires local execution

- Apply the additive schema migration to the local CSV after checking the backup.
- Manually label the test_v2 sample and any blind relabel sample.
- Run evaluator and Ollama against the local environment.
- Review measured results before changing production routing or promoting a model.
