# FRIDAY Task 2 — Private evaluation data collection

These scripts are additive. They do not change the production classifier, database, or existing `data/priority_engine_split.json` manifest.

## 1. Create or resume `test_v2`

From the FRIDAY project root in PowerShell:

```powershell
python -m scripts.sample_test_v2 --input data/email_labels.csv --split-manifest data/priority_engine_split.json --manifest data/test_v2_manifest.json --output data/test_v2_labels.csv --sample-size 60 --seed 20261026
```

- Sampling is uniform without replacement from eligible IDs, with deterministic ordering and a fixed seed.
- Every row with any non-empty `true_*` annotation is excluded.
- The union of all existing frozen test IDs is excluded, even if an ID has no current label in the CSV.
- `data/test_v2_manifest.json` stores only selected IDs and sampling metadata; no message text or labels.
- `data/test_v2_labels.csv` is a separate private annotation workspace with selected email context and blank human-label fields. It does not edit `data/email_labels.csv`.
- Reruns reuse the manifest. If the annotation CSV exists and its IDs match the manifest, it is left byte-for-byte unchanged so labeling progress survives.
- If a manifest exists with a different requested sample size, or if IDs are no longer eligible, the script fails instead of silently changing the frozen sample.

Label the separate file using the existing interactive labeler:

```powershell
python -m scripts.label_emails --input data/test_v2_labels.csv
```

Do not use `test_v2` to tune models or choose thresholds. It is reserved for final evaluation.

## 2. Sample 30 verified development emails for blind relabeling

```powershell
python -m scripts.check_label_agreement sample --input data/email_labels.csv --split-manifest data/priority_engine_split.json --manifest data/relabel_manifest.json --output data/relabel_review.csv --sample-size 30 --seed 20261027
```

The sampler selects only previously verified development emails and excludes every ID in the existing frozen test manifest. The review CSV contains message context but hides the original human labels and model predictions. It is a separate file and does not overwrite the source annotations.

Relabel the queue:

```powershell
python -m scripts.label_emails --input data/relabel_review.csv
```

Then calculate agreement:

```powershell
python -m scripts.check_label_agreement score --input data/email_labels.csv --review data/relabel_review.csv --output reports/label_agreement.json
```

The report contains per-task paired sample counts and Cohen's kappa, without email IDs or message text. Rows missing either annotation for a task are excluded from that task's kappa.

**Legacy spam caveat:** if the original CSV does not yet contain `true_is_spam`, the script derives the first spam annotation from legacy `true_priority` (`SPAM` → `yes`; a valid non-spam priority → `no`). Kappa for those rows is provisional because the first spam annotation was inferred from the old combined schema. Explicit `true_is_spam` labels take precedence.

## 3. Test

```powershell
python -m pytest -q tests/test_sample_test_v2.py tests/test_check_label_agreement.py
python -m pytest -q
```

All test fixtures use synthetic email content and IDs. Keep manifests, review CSVs, annotation CSVs, and email exports private under `data/`.
