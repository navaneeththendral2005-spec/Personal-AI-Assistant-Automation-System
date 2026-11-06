# Label provenance and the agreement gate

Ground truth for FRIDAY is **human**. This document explains the provenance controls added
to the labeling pipeline and the exact steps to run the v2.1 agreement gate.

## What was wrong

- `import_labeling_workbook` recorded every workbook as a human `blind_relabel`, including
  workbooks an AI assistant had filled in as drafts. Those rows entered the same file as
  your own labels.
- Draft detection existed only in the comparison script and depended on a `claude_draft:`
  note. Earlier drafts had some **empty** notes, so those rows could not be recognised.
- `compare_label_agreement_v2` rejects any file with more than one row per email, while the
  importer deliberately keeps several annotations per email (different guide versions or
  batches). The two could not be used together.

## What changed

| File | Change |
|---|---|
| `scripts/import_labeling_workbook.py` | **Modified.** `--labeler human\|ai_draft` is required. AI drafts (declared, or detected by a note marker such as `claude_draft:`) go to a separate quarantine file, never into the human file. One marker quarantines the **whole workbook**. Adds a `labeler` column. Refuses to merge into a pre-provenance file. |
| `scripts/compare_label_agreement_v2.py` | **Modified (additive).** Uses only `labeler=human` rows when the column exists; adds `--guide-version`, `--batch-name`, `--require-human-provenance`, `--merge-personal-into-other`; adds a confusion matrix and a `provenance_verified` flag. Defaults are unchanged. |
| `scripts/quarantine_ai_drafts.py` | **New.** One-time repair of an existing label file. Forces an explicit human/AI decision for every batch. |
| `scripts/agreement_gate.py` | **New.** Applies the pre-registered kappa rule and says which guide sections to rewrite on a failure. |
| `tests/test_import_provenance.py`, `tests/test_quarantine_ai_drafts.py`, `tests/test_agreement_gate.py` | **New.** 69 tests. |

All 394 existing tests still pass unchanged (463 in total).

## Guarantees

| Guarantee | How it is enforced |
|---|---|
| An AI draft never enters the human file | Declared drafts and note-marker drafts are routed to `data/labeling/drafts/`; tested, and checked by mutation tests |
| Unlabeled-looking AI rows are still caught | A marker on any row, including an incomplete one, quarantines the whole workbook |
| Provenance is never assumed | The quarantine tool refuses until every batch is declared draft or confirmed human |
| The gate cannot score unknown provenance | No `labeler` column means a refusal with instructions |
| Reports leak nothing | Aggregate counts only: no IDs, senders, subjects, or notes |
| Nothing is silently overwritten | Existing outputs need `--overwrite`, and a backup goes to `backups/` first |

## Workflow (PowerShell, project root, venv active)

```powershell
# 1. See what is in your label file (counts and batch names only)
python -m scripts.quarantine_ai_drafts --list

# 2. Declare EVERY batch. Use the batch names printed above.
#    --draft-batch NAME          an AI assistant filled this batch
#    --confirm-human-batch NAME  you labeled this batch yourself, blind
python -m scripts.quarantine_ai_drafts `
    --draft-batch <AI_BATCH_1> --draft-batch <AI_BATCH_2> `
    --confirm-human-batch <YOUR_BATCH> --in-place
#    (a backup of the original is written to data/backups/ first)

# 3. Label the blank v2.1 pilot workbook YOURSELF in Excel, without opening any AI-filled file.

# 4. Import your own labels
python -m scripts.import_labeling_workbook data/labeling/v21_blind_pilot_20.xlsx --labeler human --overwrite

# 5. Run the gate on your human labels only
python -m scripts.agreement_gate --guide-version v2.1 --batch-name <YOUR_PILOT_BATCH_NAME>
```

If a batch such as the v2.1 pilot was imported earlier and you are unsure who labeled it:
if it carries `claude_draft:` notes it is quarantined automatically; if you filled it in
yourself, declare it with `--confirm-human-batch`; if you imported a file an assistant gave
you, declare it with `--draft-batch`.

## The gate rule (fixed in advance)

- **Gated:** `is_spam` and `needs_response`, each needing Cohen's kappa **>= 0.6** between your
  legacy labels and your new blind human labels.
- **Reported, not gating:** `priority`, `category` (legacy PERSONAL is merged into OTHER for
  the comparison only; stored labels are never changed).
- **INCONCLUSIVE** (neither pass nor fail) when there are fewer than 20 paired rows, or when
  either rater used a single class, because kappa is then undefined.
- **FAIL** means rewrite the confusable rules, not label more. The report lists the label pairs
  you mixed up and the guide sections to revisit.
- A second view excludes rows you marked `unsure=yes`.

| Exit code | Meaning |
|---|---|
| 0 | PASS |
| 1 | FAIL |
| 3 | INCONCLUSIVE |
| 2 | Error (missing file, unknown provenance, ...) |

The 3-day self-consistency check (a fresh blind relabel against your first human round) is
a comparison **between two human rounds**. Use `python -m scripts.analyze_batch_agreement` for
it; it reads the cleaned human file unchanged.

## Not covered here

The human labels themselves. The gate measures your agreement with your own earlier labels, so
it can only run on labels you entered.

## Suggested `.gitignore` additions

```
.qodo/
*.patch
```

## Changelog entry (paste into `CHANGELOG.md`)

```
### Fixed (label provenance)
- Importer no longer records AI-drafted workbooks as human labels; adds required --labeler and a draft quarantine
- Agreement analysis uses human rows only, supports guide-version and batch selection, and reports a confusion matrix
### Added
- scripts/quarantine_ai_drafts.py: explicit human/AI declaration for every existing batch
- scripts/agreement_gate.py: pre-registered kappa gate (>= 0.6 on is_spam and needs_response)
- 69 tests covering provenance, quarantine, and the gate
```
