# Priority engine: shared features, leakage controls, and feature evaluation

This document covers the Phase 2 completion work. Everything here is **additive**: no existing
file was changed, and the original text-only baseline is still available and reproducible.

## What was added

| File | Purpose |
|---|---|
| `backend/services/email_features.py` | Single shared feature extractor (normalisation, sender/domain parsing, metadata features, Gmail-label policy, leakage guards) |
| `backend/services/priority_model.py` | One scikit-learn pipeline in three variants (legacy text-only, normalised text-only, text + metadata) and a leakage-safe `train_model` |
| `scripts/evaluate_feature_pipeline.py` | Old-vs-new comparison on development cross-validation and the frozen test split |
| `scripts/audit_priority_dataset.py` | Dataset counts, imbalance, integrity, and provenance; writes the dataset card |
| `scripts/suggest_labeling_batch.py` | Proposes unlabeled emails likely to belong to under-represented classes |
| `scripts/enrich_dataset_metadata.py` | Adds timestamp, thread, and recipient columns from the local database to a **new** CSV |
| `docs/PRIORITY_ENGINE_DATASET.md` | Generated dataset card (aggregate counts only) |
| `tests/test_*.py` (7 files) | 155 new tests |

## Checklist coverage

| Checklist item | Status | Where |
|---|---|---|
| Collect additional human-verified examples | **Needs you.** Labels must come from a person. Tooling is ready. | `suggest_labeling_batch.py` picks what to label next |
| Address class imbalance (FINANCE, NEEDS_REPLY, PERSONAL, OTHER) | Tooling and handling done; more labels still needed | `class_weight="balanced"`, audit shows exact gaps, suggester targets them |
| One canonical verified-label dataset, documented | Done | `docs/PRIORITY_ENGINE_DATASET.md`, `audit_priority_dataset.py` |
| Dataset counts and label provenance documented | Done | Dataset card + `reports/dataset_audit.json` |
| Centralised feature-extraction module | Done | `email_features.py` |
| Sender, domain, subject, snippet, body normalisation | Done | `clean_text`, `parse_sender`, `normalized_model_text` |
| Structured metadata features | Done (69 features) | `extract_features` |
| Timestamp and recency features | Done; inactive until a date column exists | `ts_*` features, `enrich_dataset_metadata.py` |
| Thread/reply metadata where available | Done; inactive until thread columns exist | `thread_*`, `is_reply`, `in_reply_to_present` |
| Attachment metadata where available | Done; the Gmail sync does not store it yet | `attachment_*`, `attachment_metadata_from_payload` |
| Combine metadata with existing TF-IDF features | Done | `text_plus_metadata` variant |
| Training and inference use identical feature definitions | Done and enforced | shared module + schema fingerprint check |
| Reuse the existing ML pipeline | Done | Same TF-IDF and logistic-regression settings; `text_only_legacy` is verified identical to the original |
| Standardise `None`, empty strings, whitespace | Done | `clean_text`, `_to_float`, `normalize_gmail_labels` |
| Malformed sender addresses and timestamps | Done, flagged with `*_invalid` features | `parse_sender`, `parse_timestamp` |
| Missing or very long bodies | Done (`body_missing`, `body_truncated`, 20,000-char cap) | `extract_features` |
| Inconsistent Gmail labels | Done | `normalize_gmail_labels` |
| Dedicated edge-case tests | Done | `tests/test_email_features.py` |
| Feature-level tests proving labels cannot enter model inputs | Done | `tests/test_feature_leakage.py` |
| Prediction columns cannot influence feature extraction | Done | allow-list `model_input_view` + tests |
| Task-specific rules for safe Gmail labels | Done | `safe_gmail_labels`, `ASSISTED_LABELS_BY_TASK` |
| Training never accesses frozen test labels | Done and enforced | `train_model`, `assert_training_excludes_frozen`, read-only manifest |
| Compare text-only baseline vs metadata + text | Done | `evaluate_feature_pipeline.py` |
| Evaluate on the existing frozen split | Done | read-only manifest |
| Document performance, class-level metrics, counts | Done | `reports/feature_pipeline_evaluation.md` (generated) |

## How leakage is prevented

1. **Allow-list.** Feature code reads only `ALLOWED_INPUT_COLUMNS` through `model_input_view`.
   `true_*`, `final_*`, `predicted_*`, `rules_*`, `llm_*`, `review*`, and `label_source` columns
   cannot influence a feature even when they sit in the same CSV row. Tests flip every one of
   them and assert the feature vectors are identical.
2. **Truth travels separately.** `train_model(rows, labels, keys=...)` receives labels as a
   separate argument, and aborts if any key is in the frozen test set.
3. **Read-only frozen split.** The evaluation script reads `data/priority_engine_split.json`
   without ever creating or repairing it.
4. **Gmail labels follow a policy.**
   - `independent` (default, used for every headline number): no Gmail label is used. Gmail's
     SPAM and CATEGORY_* labels are Google's own predictions, so using them would measure
     Google, not FRIDAY.
   - `gmail_assisted` (ablation only): only Google's classifier labels listed per task.
     STARRED, UNREAD, SENT, DRAFT, TRASH and custom user labels are never usable because they
     record human behaviour.
5. **No wall-clock time.** Recency is computed against an explicit `reference_time`
   (the latest message timestamp), never `datetime.now()`.
6. **Schema fingerprint.** A trained model refuses to predict if the feature definitions changed.

## How to run

```powershell
python -m scripts.audit_priority_dataset --write-doc docs/PRIORITY_ENGINE_DATASET.md
python -m scripts.enrich_dataset_metadata        # optional: adds date/thread/recipient columns
python -m scripts.evaluate_feature_pipeline      # add --input data/email_labels_enriched.csv after enriching
python -m scripts.suggest_labeling_batch         # then: python -m scripts.label_emails --queue data/labeling_queue_minority.csv
python -m pytest tests -q
```

`evaluate_feature_pipeline` writes `reports/feature_pipeline_evaluation.json` and `.md`
(aggregate numbers only; `reports/` is git-ignored, so copy the final table into the README
when you publish).

## Results on the current dataset (81 verified labels)

Macro-F1. Development CV is out-of-fold on development rows. The frozen test set holds
20 rows (11 for priority), so **one email moves accuracy by 5 to 9 points**.

| Task | Dev CV: legacy | Dev CV: + metadata | Frozen test: legacy | Frozen test: + metadata | Recommended (dev CV only) |
|---|---|---|---|---|---|
| priority | 0.301 | 0.284 | 0.356 | 0.349 | text-only |
| is_spam | 0.680 | 0.710 | 0.600 | 0.583 | text-only |
| needs_response | 0.595 | 0.544 | 0.498 | 0.498 | text-only |
| category | 0.308 | 0.470 | 0.217 | 0.148 | text + metadata |

**Reading these honestly:** no reliable improvement is established. Every paired-bootstrap
interval on the frozen test set includes zero. Metadata helps `category` in cross-validation
(+0.16) but not on the 20-row test set, and it slightly hurts `needs_response`. With about 60
development rows per task, none of these differences is trustworthy yet.

Why the gain is limited right now:

- **Date, thread, recipient, and attachment features are inactive.** The labeled CSV has no
  such columns. Run `enrich_dataset_metadata.py` to activate the first three.
- **Class counts are tiny.** FINANCE has 4 verified examples, PERSONAL 5, NEEDS_REPLY 6.
- **The metadata weight (0.5) and scaler (MaxAbs)** were chosen with a small sweep on
  development cross-validation only (average macro-F1 over four tasks: baseline 0.471,
  MaxAbs at 0.5 = 0.502, StandardScaler at 0.3 = 0.506). That is a weak basis, so treat both as
  defaults, not tuned values.

The per-task recommendation rule is deliberate: a metadata variant is only recommended where it
beats the baseline in development cross-validation by at least 0.03 macro-F1. The frozen test
set is never used to choose a variant.

## Limitations

- Attachment metadata is not stored by the Gmail sync. `attachment_metadata_from_payload`
  is ready; storing the result needs a migration and a sync change.
- `thread_message_count` counts locally stored messages, not the full Gmail thread.
- Keyword features overlap with the rule-based classifier by design; they are inputs, not labels.
- The 20-row test set cannot confirm small differences. Add the `test_v2` random sample
  before drawing conclusions.

## Changelog entry (paste into `CHANGELOG.md`)

```
### Added (Phase 2 completion, additive only)
- Shared leakage-safe feature extraction (`backend/services/email_features.py`)
- Text + metadata pipeline and training guards (`backend/services/priority_model.py`)
- Feature-pipeline evaluation, dataset audit, labeling-queue suggester, and metadata enrichment scripts
- Dataset card and feature documentation
- 155 new tests covering normalisation, missing/malformed metadata, and ground-truth leakage
```
