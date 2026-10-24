# FRIDAY classifier hardening and evaluation

## Included in this package

- Deadline queries accept an injectable `now` value for deterministic tests.
- Upcoming-deadline searches exclude deadlines in the past; overdue searches exclude future dates.
- Auto-labeling supports a local SQLite cache of validated Ollama outputs. Cache rows contain only a SHA-256 key, structured prediction, latency, and timestamp—not message text.
- Cache keys include model name, prompt version, sender, subject, snippet, labels, and the body text actually sent to the model. Changing content/model/prompt invalidates the cache.
- Cache failures are not stored. `--no-cache` disables caching; `--cache PATH` chooses another local cache file.
- Progress displays row number and estimated remaining time; the final summary includes cache hits, actual Ollama calls, fallbacks, mean successful latency, elapsed time, and review count.
- Ollama failures print a bounded error detail and store the diagnostic in `review_reason`. Do not share CSVs containing private email content.
- `scripts/evaluate_verified_labels.py` compares the rules prediction and the chosen prediction to human-entered `true_*` labels only. It never calls Gmail or Ollama and never fills missing ground truth.

## Run the workflow

From the repository root in PowerShell:

```powershell
python -m pytest -q
python -m scripts.auto_label_emails --input data/email_labels.csv
```

First run may call Ollama for all rows. Later runs reuse cached successful results when the classification inputs/model/prompt version are unchanged. To force fresh model inference, use `--no-cache` for a run (this disables reading and writing the cache); to remove the existing cache, stop running jobs and delete `data/.friday_ollama_cache.sqlite3` manually.

Rules-only mode remains available:

```powershell
python -m scripts.auto_label_emails --input data/email_labels.csv --no-llm
```

## Verified evaluation

Only inspectable human labels in `true_priority`, `true_needs_response`, and `true_category` count as ground truth. Predictions, model confidence, and rules output must never be copied into these columns automatically.

```powershell
python -m scripts.evaluate_verified_labels --input data/email_labels.csv
```

Writes a JSON report and a Markdown summary under `reports/`. These outputs are local/ignored. Tasks with fewer than two verified labels are marked insufficient. Tasks below the configured minimum (20 by default) are marked exploratory. A single deterministic holdout is a starting comparison, not a definitive performance claim; collect representative labels and use a frozen test set for final reporting.

## Selective review policy

- Any disagreement between rules and Ollama remains reviewable.
- Low confidence remains reviewable; model-reported confidence is not calibrated probability.
- A deterministic random sample can be reviewed to detect shared blind spots.
- Do not reduce the queue by silently choosing a winner or marking predictions as verified.
- Review and correct labels in the labeling workflow; keep the three `true_*` columns human-owned.

## Production integration gate

Do **not** switch the production email analysis path to Ollama solely because requests succeed. The current dataset has too few verified labels to establish a winner. Once a representative labeled set exists, compare rules and Ollama on the same frozen holdout for all supported tasks, inspect class-level errors, latency, fallback rate, and privacy requirements. Only then decide whether to integrate a selected model and record method/version in persisted analysis. No production classifier switch or database migration is included in this package because available evidence does not justify choosing a winner yet.

## Privacy

Email exports, review queues, backups, evaluation reports, and cache files are local artifacts and must not be committed. The cache deliberately avoids storing raw email text, but it is still a private local artifact because it stores predictions and timing metadata. Ollama inference is configured for the local Ollama endpoint; separately configured external-provider features elsewhere in FRIDAY are not changed by this package.
