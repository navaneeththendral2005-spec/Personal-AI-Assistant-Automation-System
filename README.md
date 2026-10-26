# FRIDAY — Personal AI Assistant

FRIDAY is a local-first personal assistant API built with FastAPI, SQLAlchemy, PostgreSQL, Alembic, and Gmail OAuth. It currently combines task/reminder management, Gmail metadata synchronization, explainable email intelligence, career-signal detection, and assistant queries over locally stored records.

## Current capabilities

- Task CRUD and scheduled reminders.
- Gmail OAuth integration and read-only message metadata access.
- Gmail metadata and readable email-body sync into the local `emails` table.
- Recursive Gmail MIME extraction, including HTML-only messages and messages incorrectly labelled as plain text.
- A dry-run-first repair utility for previously stored email bodies that contain raw HTML or unusual Unicode spacing.
- Rule-based email categorization (`JOB`, `FINANCE`, `PERSONAL`, `PROMOTIONS`, `UPDATES`, `OTHER`), priority scoring, response/action evidence, and conservative deadline detection.
- Career-related email signals and career stages.
- Inbox insights, full-stored-body summaries with snippet fallback, body-aware inbox search and question answering, and explicit task creation from a selected email.
- Optional OpenAI-compatible summary/answer provider; local behavior remains available without an AI key.

**Important limitations:** the current classifier is heuristic and has not been assigned an accuracy score from a manually labeled evaluation set. Classification and inbox summaries can use locally stored email body text when available, with snippets as fallback. Attachments are not parsed or analyzed. Treat detected deadlines/actions as suggestions and verify the original email.

## Requirements

- Python 3.11+ recommended
- PostgreSQL
- Gmail OAuth credentials configured locally for Gmail features

## Setup (Windows PowerShell)

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env` and set `DATABASE_URL` to your PostgreSQL database. Do not put OAuth secrets or API keys in `.env.example`. Gmail OAuth credential files belong in the ignored local `secrets/` directory.

Apply database migrations:

```powershell
alembic upgrade head
```

Start the API:

```powershell
python -m uvicorn backend.main:app --reload
```

Open `http://127.0.0.1:8000/docs` for interactive API documentation. Keep the server bound to localhost unless you add authentication and deployment hardening. The API currently should be treated as a local development application, not exposed publicly.

## Email intelligence workflow

1. Complete Gmail OAuth locally.
2. Sync recent metadata using `POST /gmail/sync` or `POST /gmail/intelligence/sync-and-analyze`.
3. Analyze already stored email content (sender, subject, snippet, labels, and body text when available) using `POST /gmail/intelligence/analyze`.
4. Query `/gmail/intelligence/emails` and `/gmail/intelligence/insights`.
5. Check the source email before acting on any inferred deadline or action.

Analysis and summary endpoints do not modify Gmail messages. Creating a task from an email is an explicit API action.


## Email body extraction and repair

FRIDAY prefers a usable `text/plain` MIME part, falls back to HTML converted to readable text, handles nested MIME alternatives, and converts HTML documents that a sender incorrectly labels as `text/plain`. Invisible formatting characters and unusual Unicode spaces are normalized without flattening paragraph breaks. Gmail access remains read-only; only the local database is updated.

If earlier syncs stored raw HTML or unusual Unicode spacing in `emails.body_text`, first inspect candidates with a dry run:

```powershell
python scripts/repair_email_bodies.py
```

Then explicitly apply repairs to the local database:

```powershell
python scripts/repair_email_bodies.py --apply
```

Optionally restrict a run with `--limit 20`. The repair script fetches each candidate by its stored Gmail message ID, updates only the local `body_text` field, and never sends, labels, archives, or deletes Gmail messages. Back up your database before applying bulk repairs.

## Tests

Run the automated tests from the project root (the test suite uses isolated SQLite databases and does not need your production database URL):

```powershell
python -m pytest -q
```

The test configuration forces a SQLite URL for the test process and each database test uses an isolated in-memory engine. It does not connect to your configured PostgreSQL database.

## Phase 3: classifier evaluation

A labeled CSV template is available at `data/email_labels_template.csv`. The template contains only headers; do not populate it with real personal email unless you are comfortable storing that data locally. Prefer anonymized or synthetic examples.

Install optional ML dependencies:

```powershell
pip install -r requirements-ml.txt
```

After you have manually labeled a sufficiently varied dataset, run:

```powershell
python scripts/evaluate_email_classifier.py --input data/email_labels.csv --output reports/classifier_evaluation.json
```

The script compares the current rules with a TF-IDF + Logistic Regression baseline using a held-out stratified test split, and reports accuracy, macro precision/recall/F1, per-class metrics, and a confusion matrix. It refuses tiny/invalid datasets rather than reporting misleading scores. Keep the dataset balanced and representative; around 200 labeled messages is a starting point, not a guarantee of reliable results. Never claim metrics before running the evaluation on valid labels.

## Configuration reference

- `DATABASE_URL` — required SQLAlchemy database URL.
- `AI_API_KEY` — optional provider key.
- `AI_BASE_URL` — optional OpenAI-compatible API base URL.
- `AI_MODEL` — optional model identifier; when either key or model is missing, the local summary path is used.

## Security and privacy

- Never commit `.env`, Gmail OAuth credentials/tokens, database files, or real mailbox exports.
- Do not expose this unauthenticated development API to the public internet.
- The classifier and optional model output can be wrong; verify important decisions.
- Do not send real email content to an external model unless you have explicitly configured and approved that data flow.


## Phase 4: concise inbox summaries and opportunity extraction

The local `/gmail/intelligence/summary?use_ai=false` path now produces a short, structured digest with separate sections for job/internship opportunities, explicit application-deadline evidence, potentially actionable messages, and other messages. It selects informative sentences instead of returning whole email bodies.

For opportunity-related questions, `/gmail/intelligence/ask` returns concise per-email findings and attempts to extract:
- role or opportunity subject,
- sender and a clearly labeled approximate company-name inference from the sender domain,
- eligibility/qualification evidence when stated,
- application link when present,
- deadline evidence only when deadline/action language is connected to a date.

If no explicit deadline evidence is found, the response says so rather than treating the email's received date or an unrelated date as the deadline. The extractor is heuristic and can miss or misinterpret unusual wording; verify consequential details in the source email. Attachments are not analyzed. When an external AI provider is configured, bounded local email context may be sent to that provider; the local fallback does not require an external provider.


## Optional local classifier experiment (Phase 2)

The production API continues to use the rules classifier until a representative, human-verified evaluation supports a change. Ollama is an **offline labeling/evaluation candidate**, not automatically promoted to production. The labeling script records rules predictions, direct Ollama predictions, the selected provisional prediction, source/status, confidence, latency, and bounded failure details separately. It never fills `true_*` fields.

Install the optional ML package and install/run Ollama locally with a model you have pulled (default model: `qwen3:4b`). Then run from the project root:

```powershell
# First verify the local Ollama service/model separately. This makes real local inference requests.
python -m scripts.auto_label_emails --input data/email_labels.csv --model qwen3:4b

# Inspect label_source, llm_status, llm_* columns, and review queue before accepting any labels.
# Evaluate only after manually entering verified true_* labels.
python -m scripts.evaluate_priority_engine --input data/email_labels.csv --split-manifest data/priority_engine_split.json
python -m scripts.evaluate_priority_engine_advanced --input data/email_labels.csv --split-manifest data/priority_engine_split.json
```

The evaluator compares the rules baseline, stored selected predictions, direct stored Ollama predictions (when available), and a TF-IDF + Logistic Regression baseline on the same frozen holdout. Missing Ollama predictions count as invalid predictions rather than disappearing from the report. Reports contain aggregate metrics only; the split manifest and email CSV remain private under `data/`. Do not tune against the frozen test set or treat generated predictions as ground truth.

The project retains multiple evaluation entry points for backward compatibility: the standard evaluator is the main comparison, the advanced evaluator adds development cross-validation and bootstrap intervals, and the verified-label script is a lightweight compatibility report. The older category-only evaluator is retained for compatibility with existing workflows; new comparisons should use `evaluate_priority_engine.py`.


## Safe Ollama evaluation and hybrid routing

To measure Ollama against human-verified labels without modifying `data/email_labels.csv`, run the dedicated isolated evaluator:

```powershell
python -m scripts.evaluate_ollama_verified --input data/email_labels.csv --output reports/ollama_verified_evaluation.json --split-manifest data/priority_engine_split.json
```

It copies only rows with at least one non-empty `true_*` value into a temporary workspace, runs local Ollama there, and writes aggregate-only JSON/Markdown reports. The original CSV is left untouched. The private split manifest stays under `data/`; never share or commit it. The first run may create the frozen holdout. If Ollama is unavailable, the command reports an error and the original dataset remains unchanged. A small holdout is exploratory, not proof of model quality.

For a hybrid labeling run that calls Ollama only when the rules confidence is below the threshold:

```powershell
python -m scripts.auto_label_emails --input data/email_labels.csv --model qwen3:4b --llm-only-when-low-confidence --confidence-threshold 0.65
```

Rows where rules confidence meets the threshold are marked `label_source=rules_confident` and `llm_status=skipped_confident_rules`. This is an optional experiment only; the production API still uses its existing rules classifier. The threshold is a routing heuristic, not a calibrated probability. Compare macro-F1, per-class recall, coverage, fallback rate, and latency before considering any production change.
