# FRIDAY Phase 2 labeling workflow corrections

This patch aligns the human-labeling tools with the frozen `docs/EMAIL_LABELING_GUIDE.md` v2.1 semantics and protects the existing frozen split.

## Important category correction

The v2.1 guide explicitly removes `PERSONAL` as a current category and maps the *legacy* `PERSONAL` concept to `OTHER`. Therefore:

- Current v2.1 categories are `JOB`, `FINANCE`, `PROMOTIONS`, `UPDATES`, and `OTHER`.
- The current split-schema labeler and workbook dropdown do not offer `PERSONAL`.
- The importer rejects `PERSONAL` for v2.1 workbooks, while preserving it for historical `v2.0-draft` workbooks.
- Legacy label files can still be read as legacy data. A legacy `PERSONAL` row is incomplete under v2.1 and is eligible for a human re-review specifically into `OTHER`; it is never silently converted.

This corrects the earlier mistaken suggestion that `PERSONAL` should be added to the v2.1 dropdown. Do not add it back unless the guide is formally versioned and changed.

## Sampler corrections

`scripts.suggest_labeling_batch` now:

- Samples `priority` and `category` independently.
- Allows an email with labels for other tasks to be considered when the requested task is still unlabelled.
- Excludes the union of all frozen test IDs from the existing manifest. The manifest is required and read-only; a missing or malformed manifest stops sampling.
- Samples priority only when priority is blank and spam is not `yes`.
- Includes `UPDATES` in category collection.
- Does not treat legacy `PERSONAL` as a v2.1 class; legacy `PERSONAL` candidates are routed only to human re-review as `OTHER`.
- Emits candidate-only queue files with no email content and no ground-truth assignments.
- Writes queue files atomically and backs up an existing queue before explicit replacement.

## Workbook exporter corrections

`scripts.export_labeling_workbook` now:

- Uses the five v2.1 categories.
- Accepts task-specific queues containing partially labeled records when the target task is still missing.
- Excludes every frozen test ID before creating a minority workbook.
- Validates the queue task and focus-class values; mixed or malformed queues are rejected.
- Keeps queue metadata out of the workbook itself.

## Other reviewed files

- `scripts.priority_engine_splits.py`: reviewed; its existing manifest behavior preserves frozen test IDs and assigns newly verified rows to development. No change required.
- `scripts.prepare_priority_engine_dataset.py`: reviewed; it validates existing manifest membership and does not regenerate or edit the manifest. Its legacy category normalization is intentionally retained for historical adjudication/evaluation data.
- `scripts.label_emails.py`: current split-schema choices now match v2.1; legacy mode still recognizes historical `PERSONAL` values so old files remain readable.

## Commands

Run from the FRIDAY repository root after applying the patch.

Generate priority candidates:

```powershell
python -m scripts.suggest_labeling_batch `
  --task priority `
  --output data/labeling_queue_priority.csv
```

Generate category candidates:

```powershell
python -m scripts.suggest_labeling_batch `
  --task category `
  --output data/labeling_queue_category.csv
```

Create a workbook from either queue:

```powershell
python -m scripts.export_labeling_workbook `
  --mode minority `
  --queue data/labeling_queue_priority.csv `
  --batch-name priority_followup_01 `
  --batch-size 40
```

For the category queue, change the queue path and batch name accordingly. Do not use the same queue output path for both tasks.

Run focused tests:

```powershell
python -m pytest -q tests/test_suggest_labeling_batch.py tests/test_export_labeling_workbook.py tests/test_import_labeling_workbook.py tests/test_labeling_schema_alignment.py
```

Then run the full suite:

```powershell
python -m pytest -q
```

The patch never reads, edits, packages, or overwrites private `data/` or `reports/` files.
