# Phase 2 — Priority Engine workflow

This workflow improves FRIDAY's email classifier without treating model output as ground truth or changing production classification before there is evidence to justify a change.

## 1. Protect private data

`data/`, local SQLite databases, and generated reports are excluded from Git. Never commit real email bodies, labels, review queues, OAuth tokens, or `.env`.

## 2. Generate provisional predictions locally

With Ollama installed and a model downloaded:

```powershell
$env:OLLAMA_MODEL = "qwen3:4b"
python -m scripts.auto_label_emails --input data/email_labels.csv
```

Rules-only mode:

```powershell
python -m scripts.auto_label_emails --input data/email_labels.csv --no-llm
```

Predictions in `predicted_*` are provisional. Only a human should fill `true_*`. Ollama runs locally by default; the script does not call Gmail or write to Gmail.

## 3. Review the queue, saving progress

```powershell
python -m scripts.label_emails --input data/email_labels.csv --queue data/email_review_queue.csv --limit 25
```

The queue is a candidate list, not an assertion that its predictions are wrong. The labeler shows predictions only as suggestions and never copies them into `true_*` automatically. It saves the CSV atomically after each email and resumes by skipping fully labeled records.

To label all incomplete rows:

```powershell
python -m scripts.label_emails --input data/email_labels.csv
```

## 4. Evaluate only verified labels

Install the optional ML dependencies:

```powershell
pip install -r requirements-ml.txt
```

Then run:

```powershell
python -m scripts.evaluate_priority_engine --input data/email_labels.csv
```

Outputs:
- `reports/priority_engine_evaluation.json`
- `reports/priority_engine_evaluation.md`

The evaluator uses only non-empty, valid `true_*` values. It reports `insufficient_verified_data` rather than pretending a tiny sample is a valid model comparison. When there is enough data, it makes a deterministic stratified holdout and trains TF-IDF + Logistic Regression only on the training split. Reports contain metrics and class counts, not raw email content or message IDs.

## 5. Production classifier gate

The production email-intelligence service deliberately remains on the explainable rules classifier (`classifier_method=rules`, `classifier_version=friday-rules-v1`) until the verified evaluation supports a change. The metadata migration records which classifier generated an analysis. Do not promote an LLM or ML model based only on confidence, disagreement, or unlabeled examples.

## Suggested labeling strategy

Start with a representative batch (for example, 25–50 emails), label priority, response requirement, and category based on the message itself, and include difficult examples rather than only obvious messages. Review rare labels separately. The right dataset size depends on class prevalence and the uncertainty in the measured metrics; a target of 100 is an initial milestone, not a statistical guarantee.

## Verification

```powershell
python -m pytest tests/test_email_labeling_workflow.py tests/test_auto_label_emails.py tests/test_priority_engine_evaluation.py -q
python -m pytest -q
```
