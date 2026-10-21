# FRIDAY — Personal AI Assistant

FRIDAY is a local-first personal assistant API built with FastAPI, SQLAlchemy, PostgreSQL, Alembic, and Gmail OAuth. It currently combines task/reminder management, Gmail metadata synchronization, explainable email intelligence, career-signal detection, and assistant queries over locally stored records.

## Current capabilities

- Task CRUD and scheduled reminders.
- Gmail OAuth integration and read-only message metadata access.
- Gmail metadata sync into the local `emails` table.
- Rule-based email categorization (`JOB`, `FINANCE`, `PERSONAL`, `PROMOTIONS`, `UPDATES`, `OTHER`), priority scoring, response/action evidence, and conservative deadline detection.
- Career-related email signals and career stages.
- Inbox insights, metadata/snippet summaries, search, and explicit task creation from a selected email.
- Optional OpenAI-compatible summary/answer provider; local behavior remains available without an AI key.

**Important limitations:** the current classifier is heuristic and has not been assigned an accuracy score from a manually labeled evaluation set. Current email intelligence is based on stored sender, subject, snippet, and Gmail labels; it does not imply full-body or attachment understanding. Treat detected deadlines/actions as suggestions and verify the original email.

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
3. Analyze already stored email metadata using `POST /gmail/intelligence/analyze`.
4. Query `/gmail/intelligence/emails` and `/gmail/intelligence/insights`.
5. Check the source email before acting on any inferred deadline or action.

Analysis and summary endpoints do not modify Gmail messages. Creating a task from an email is an explicit API action.

## Tests

Run the automated tests from the project root:

```powershell
python -m pytest -q
```

Some tests may require environment variables or a configured test database depending on the local setup. Do not point tests at a production database.

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
