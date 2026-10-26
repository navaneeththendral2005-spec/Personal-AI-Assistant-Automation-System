# FRIDAY Phase 2 — Priority Engine: Implementation Plan and Acceptance Checklist

## Objective

Finish Phase 2 as a reliable, measurable email-classification system that fits the existing FRIDAY architecture. The goal is not to maximize a single accuracy number; it is to make labels consistent, protect the evaluation set, compare approaches honestly, and ship a safe production decision path.

## Classification contract

FRIDAY treats these as separate decisions:

- **Spam:** `true_is_spam` = `yes` / `no`.
- **Priority:** `true_priority` = `URGENT` / `NEEDS_REPLY` / `INFORMATIONAL`.
- **Response required:** `true_needs_response` = `yes` / `no`.
- **Category:** `true_category` = `JOB` / `FINANCE` / `PERSONAL` / `PROMOTIONS` / `UPDATES` / `OTHER`.

Legacy rows with `true_priority=SPAM` are not rewritten. The migration adds `true_is_spam` where it can derive a value, and the evaluator excludes legacy SPAM rows from priority scoring while evaluating spam separately. Promotions are not automatically spam. A Gmail `SPAM` label is a useful explicit signal for the rules baseline; it is not evidence that every promotional message is spam.

## What this overlay implements

1. Adds a dedicated `is_spam` evaluation task based on `true_is_spam`.
2. Defines priority evaluation using the three non-spam priority labels.
3. Keeps the historical four-label split schema when reading/creating the existing priority holdout assignment, preserving frozen IDs instead of regenerating the holdout due to a taxonomy clarification.
4. Supports dedicated `rules_is_spam`, `predicted_is_spam`, and `llm_is_spam` prediction columns, with a conservative legacy fallback for older CSV exports.
5. Emits separate spam decision fields from the auto-labeling adapter while retaining legacy `priority=SPAM` output for compatibility.
6. Reports a clear `insufficient_training_classes` state if a frozen split leaves only one class in development data instead of surfacing an opaque model-training failure.
7. Extends focused regression tests and provides this plan, rollout guide, and acceptance checklist.

## Stage A — Data foundation

### Work
- Inspect the exact current header and existing backup state before migration.
- Run the additive `true_is_spam` migration. It must be idempotent, preserve existing explicit `true_is_spam` values, and never rewrite `true_priority`.
- Validate allowed values: `true_is_spam` is blank, `yes`, or `no`; priority is blank or one of the supported legacy/current values.
- Keep human annotations distinct from model-generated suggestions.

### Acceptance criteria
- `true_is_spam` exists.
- Existing human-entered values are preserved.
- A second migration run produces no semantic change.
- No raw email body or identifier is copied into reports.
- A pre-existing backup is never overwritten.

### Command (from the project root)
```powershell
# First inspect whether a backup already exists; migration refuses to overwrite one.
Test-Path .\data\email_labels.csv.bak
python -m scripts.migrate_email_label_schema --input data/email_labels.csv
```
If the backup already exists, stop and preserve it. Choose a separate verified backup location before using `--no-backup`; do not delete a backup just to make the command pass.

## Stage B — Evaluation dataset and holdout integrity

### Work
- Preserve `data/priority_engine_split.json` and all existing task test IDs.
- Use the existing `scripts/sample_test_v2.py` workflow for a separate blind review sample.
- Exclude all frozen holdout IDs from relabel sampling.
- Use `scripts/check_label_agreement.py` for independent blind relabeling and agreement statistics.
- Keep human labels as the only ground truth; predictions must never fill truth fields automatically.

### Acceptance criteria
- The frozen manifest is valid, private, and unchanged unless a deliberate, documented versioned evaluation reset is approved.
- The `test_v2` sample manifest is immutable on rerun.
- Relabel samples exclude frozen holdout IDs.
- Reports contain aggregate counts/metrics, not raw IDs or message content.
- No test-set label is used to tune rules, prompts, thresholds, or model selection.

### Commands
```powershell
python -m scripts.sample_test_v2 --input data/email_labels.csv --split-manifest data/priority_engine_split.json --manifest data/test_v2_manifest.json --output data/test_v2_labels.csv --sample-size 60 --seed 20261026
```
Label `data/test_v2_labels.csv` manually. Do not synthesize labels or copy predictions into `true_*` fields.

For blind agreement sampling, inspect the CLI first:
```powershell
python -m scripts.check_label_agreement --help
```

## Stage C — Rules baseline

### Work
- Keep deterministic rules explainable and conservative.
- Use available sender, subject, snippet, body, Gmail labels, and metadata only.
- Keep spam separate from priority. Promotions/newsletters are not automatically spam.
- Keep urgency separate from response requirement: a message may need a reply without being urgent.
- Treat model outputs as untrusted input and validate all enum values before saving.
- Preserve the existing production classifier contract until the new evaluation is reviewed.

### Acceptance criteria
- All outputs are in their documented enums.
- Missing/invalid predictions are counted as errors, not silently dropped.
- `rules_is_spam` is populated independently where available.
- Rules output is deterministic for the same input.
- Tests cover explicit spam labels, promotions, urgent requests, reply requests, and ambiguous examples.

## Stage D — Metadata and text features

### Candidate features
- Sender/domain and whether the sender is known.
- Subject, snippet, and bounded body text.
- Gmail labels such as `INBOX`, `UNREAD`, and explicit `SPAM`.
- Reply/thread indicators and attachment presence only where available in the actual schema.
- Message recency only when timestamp quality is known.

### Guardrails
- Never use `true_*`, `label_source`, review decisions, model predictions, or post-outcome fields as training features.
- Do not use private identifiers as predictive features.
- Avoid features that are unavailable in production.
- Document missing-value behavior and normalize metadata consistently.

### Acceptance criteria
- Feature extraction is tested independently.
- The exact same feature builder is used for development and inference.
- No ground-truth leakage.
- Feature additions are compared on the same frozen holdout, once, after development tuning is complete.

## Stage E — ML baselines

### Required comparisons
1. Existing deterministic rules.
2. TF-IDF + Logistic Regression.
3. A hybrid approach that uses rules for clear cases and an ML classifier for ambiguous cases.

### Evaluation protocol
- Train only on development rows.
- Keep the frozen holdout untouched.
- Report accuracy, macro-F1, per-class precision/recall/F1, support, confusion matrix, and prediction coverage.
- Missing/invalid predictions count as errors.
- State `insufficient_verified_data`, `insufficient_training_classes`, or `unable_to_split` rather than claiming quality when the data or split cannot support it.
- Report uncertainty where sample size allows; do not over-interpret small holdouts.
- Do not promote a model solely because its aggregate accuracy is higher.

### Acceptance criteria
- All approaches use the same fixed holdout IDs and same label definitions.
- Every result includes training/test counts and the model/config version.
- A model promotion decision is recorded with per-class trade-offs.

## Stage F — Ollama and hybrid router

### Work
- Keep Ollama local and make its availability optional.
- Use a strict JSON schema, bounded email-body input, timeout, and enum validation.
- Treat email content as untrusted data, never as instructions to the classifier.
- Route only ambiguous/low-confidence cases to Ollama; rules remain the fallback if Ollama is unavailable or invalid.
- Do not execute email instructions, send replies, or modify Gmail as a side effect of classification.
- Use cache keys that include model and prompt version; avoid storing unnecessary message content in reports.

### Acceptance criteria
- Timeout, connection error, malformed JSON, missing keys, invalid enums, and invalid confidence are tested.
- Fallback is deterministic and logged without leaking message contents.
- The hybrid router is compared with rules and ML on the same frozen holdout before production promotion.
- Production stays on the current known-good path until results justify a change.

## Stage G — Release verification

Run from the correctly structured project root:

```powershell
python -m pytest -q
python -m scripts.evaluate_priority_engine --input data/email_labels.csv --output reports/priority_engine_evaluation.json --split-manifest data/priority_engine_split.json
python -m scripts.evaluate_priority_engine_advanced --input data/email_labels.csv --output reports/priority_engine_advanced_evaluation.json --split-manifest data/priority_engine_split.json
```

If `scikit-learn` is missing:
```powershell
python -m pip install -r requirements-ml.txt
```

### Release gate
- All tests pass.
- The migration is complete and idempotent.
- Evaluation outputs explicitly report data sufficiency and the spam/priority separation.
- The frozen manifest has not been regenerated.
- Aggregate reports contain no email content or raw identifiers.
- Production routing changes are documented and backed by measured evaluation.
- The README and changelog match actual behavior.

## Completion definition

Phase 2 is complete only when Stages A–G are verified in the actual local project, the evaluation reports are reproducible, and the production path has a tested fallback. This overlay delivers the evaluator/schema integration and regression coverage; data migration, human labeling, local Ollama execution, and final release-gate commands must still be run against the user's real local project because the private dataset and local runtime are intentionally not bundled.
