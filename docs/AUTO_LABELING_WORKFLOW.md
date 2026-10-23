# FRIDAY provisional auto-labeling workflow

This tool speeds up dataset preparation without pretending predictions are ground truth.
It runs FRIDAY's current rules and (unless `--no-llm` is supplied) a local Ollama model,
then puts disagreements, low-confidence results, and a deterministic random quality sample
into `data/email_review_queue.csv`.

## Run it

From the FRIDAY project root in PowerShell:

```powershell
# Optional: make sure the local model is available first
ollama pull qwen3:4b

# Generate provisional predictions and the selective-review queue
$env:OLLAMA_MODEL = "qwen3:4b"
python -m scripts.auto_label_emails --input data/email_labels.csv

# If Ollama isn't installed/running, generate rule-only provisional predictions
python -m scripts.auto_label_emails --input data/email_labels.csv --no-llm

# Run tests (the tests use fakes; they do not call Ollama)
python -m pytest tests/test_auto_label_emails.py -q
python -m pytest -q
```

## Outputs

- `data/email_labels.csv`: original `true_*` fields are preserved. New `predicted_*`, `rules_*`, confidence, source, latency, and review fields are added.
- `data/email_review_queue.csv`: messages requiring review, with reason and current predictions.

All predictions are **provisional**. The script never fills the `true_*` columns. Review the queue and copy a decision into `true_*` only after verifying it against the message. A small random sample is included to catch cases where both models agree but are wrong. Tune `--confidence-threshold`, `--review-sample-rate`, and `--seed` if needed; keep the seed fixed for reproducibility.

## Privacy

Both CSVs contain private email content. Keep them local and uncommitted. Add/retain `data/` in `.gitignore`. Ollama is called only at `OLLAMA_HOST` (default `http://localhost:11434`); do not point it at a remote service if you require local-only processing. The script only reads the CSV and never modifies Gmail.

## Design notes

- Rules run first as a cheap fallback.
- Local Ollama predictions are preferred for the provisional label when available.
- Any disagreement on priority, response need, or category is queued for review.
- A low-confidence result or a deterministic random sample is also queued.
- The tool deliberately separates `predicted_*` from `true_*` to avoid evaluation leakage.
- `llm_latency_ms` records per-email latency; whether inference ran fully on GPU is not inferred because Ollama's response payload does not reliably establish that. Check local Ollama logs/telemetry separately when benchmarking hardware placement.
