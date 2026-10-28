# FRIDAY — Personal AI Assistant

FRIDAY is a personal AI assistant project designed to bring email intelligence, task management, reminders, and career opportunity tracking into one application.

The project combines a Python backend, Gmail integration, email classification, and a lightweight web dashboard to help users organize information and act on important messages.

## Features

* **Email Intelligence:** Process email content and extract useful information from messages.
* **Priority Classification:** Classify emails by priority using rule-based and machine-learning approaches.
* **Spam Detection:** Identify potential spam messages.
* **Response Detection:** Estimate whether an email requires a reply.
* **Email Categorization:** Organize messages by their primary purpose.
* **Gmail Integration:** Support Gmail authentication, synchronization, and email processing.
* **Task Management:** Organize tasks and reminders.
* **Career Tracking:** Support career opportunity discovery and related action tracking.
* **Web Dashboard:** Access assistant functionality through a browser-based interface.
* **Evaluation and Testing:** Use automated tests and evaluation scripts to assess behavior.

> Classification quality varies by task. Model evaluation and improvement remain part of the project's development.

## Technology Stack

* Python
* FastAPI
* SQLAlchemy and Alembic
* Gmail API and OAuth authentication
* scikit-learn and TF-IDF for machine-learning experiments
* HTML, CSS, and JavaScript
* pytest

## Project Structure

```text
FRIDAY/
├── backend/
│   ├── routers/
│   ├── services/
│   └── dashboard/
├── docs/
├── migrations/
│   └── versions/
├── reports/
├── scripts/
├── tests/
├── requirements.txt
├── requirements-ml.txt
├── alembic.ini
├── .env.example
└── README.md
```

Private datasets, local databases, credentials, and evaluation split manifests are intentionally excluded from the public project.

## Getting Started

### 1. Clone the repository

```bash
git clone <YOUR_GITHUB_REPOSITORY_URL>
cd FRIDAY
```

Replace the placeholder with the actual repository URL after publishing the project.

### 2. Create a virtual environment

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### 3. Install dependencies

```powershell
python -m pip install -r requirements.txt
```

For machine-learning evaluation tools, install the additional dependencies:

```powershell
python -m pip install -r requirements-ml.txt
```

### 4. Configure environment variables

Create a local `.env` file using `.env.example` as a reference.

Fill in the required values for your environment. Never commit `.env`, OAuth credentials, access tokens, or other secrets.

Gmail integration may require Google Cloud project configuration and OAuth credentials. Store credentials locally in the locations expected by the application.

### 5. Run the application

Start the FastAPI application using the development server:

```powershell
uvicorn backend.main:app --reload
```

Review the application routes and configuration if additional services or environment variables are required.

## Testing

Run the complete test suite:

```powershell
python -m pytest -q
```

Run individual test modules when working on specific functionality:

```powershell
python -m pytest -q tests/test_email_labeling_schema.py
python -m pytest -q tests/test_email_labeling_workflow.py
python -m pytest -q tests/test_priority_engine_splits.py
```

The project has previously passed 176 automated tests. Run the suite against the current checkout to verify its status.

## Email Labeling and Evaluation

FRIDAY's classifier evaluation uses human-verified `true_*` fields as ground truth. Model predictions must remain separate from human annotations.

Development cross-validation is used for model selection, while frozen holdout assignments are reserved for independent evaluation. Private evaluation manifests and email datasets should not be published.

Refer to `docs/EMAIL_LABELING_GUIDE.md` for annotation rules and the other documents in `docs/` for implementation and evaluation details.

## Security and Privacy

* Never commit `.env` files containing real credentials.
* Never publish Gmail OAuth tokens or client secrets.
* Keep personal email datasets, review CSVs, and local databases private.
* Keep frozen test manifests private to avoid compromising evaluation integrity.
* Review Git changes before committing or pushing.

## Project Status

FRIDAY is an actively developed personal AI assistant. Features, integrations, and classification quality may evolve as implementation and evaluation continue.

## License

No license has been specified yet. Add a license before distributing the project for reuse.
