# FRIDAY Project Review — Gmail Body Extraction

## Scope reviewed

Reviewed the uploaded project archive across the backend API, Gmail OAuth/service/sync modules, email intelligence and career services, ORM models, Alembic migrations, dashboard assets, scripts, requirements, configuration, and tests. The original archive contains source/configuration only; it does not contain a real `.env`, OAuth token, or database file.

## Root cause

The Gmail body extractor treated every `text/plain` MIME part as actual plain text. Some senders provide a full HTML document while incorrectly labelling that part `text/plain`. That path returned the decoded HTML unchanged, which then flowed through `normalize_gmail_message()` into `EmailMessage.body_text` and was persisted by the synchronization upsert. Since the stored row then matched the same fetched raw HTML, later syncs reported it as unchanged.

A second issue was that HTML-to-text conversion inserted literal newline strings and then used BeautifulSoup `strip=True`, which could discard those separators and flatten paragraphs. Unicode layout spaces/BOM/zero-width characters were also not normalized consistently.

## Changes implemented

- `backend/services/gmail_service.py`
  - Handles nested MIME leaf parts recursively.
  - Prefers usable plain text and falls back to HTML.
  - Detects HTML documents/fragments even when they are incorrectly labelled `text/plain` and converts them to readable text.
  - Loads text-part content through Gmail's attachment endpoint when Gmail supplies an `attachmentId` instead of inline body data.
  - Preserves paragraph/list line breaks during HTML conversion.
  - Normalizes BOM, zero-width formatting characters, and unusual Unicode spacing.
  - Adds `get_email_by_id()` for safe read-only refresh of a stored message.
  - Imports Google API/OAuth dependencies lazily so pure extraction tests can run without OAuth configured.
- `scripts/repair_email_bodies.py`
  - Finds locally stored body values that contain raw HTML or nonstandard Unicode spacing.
  - Defaults to dry-run mode; `--apply` is required to update the local database.
  - Fetches messages using Gmail read-only GET calls and updates only local `emails.body_text`.
- `tests/test_gmail_body_extraction.py`
  - Adds regression coverage for mislabeled HTML, HTML-only content, nested MIME structures, attachment-backed body data, Unicode whitespace, and paragraph/list formatting.
- `tests/conftest.py`
  - Sets a test-only SQLite URL before application imports. Individual database tests still create isolated in-memory SQLite engines; the test process does not use a configured PostgreSQL URL.
- `README.md`
  - Documents the extraction behavior and safe repair workflow.

## Expected behavior now

- `list_emails()` returns a `body_text` value containing readable text rather than raw `<!DOCTYPE>`, `<html>`, `<head>`, or similar markup for the supported cases.
- Plain-text alternatives remain preferred when available.
- HTML-only or mislabelled HTML emails are converted to text; paragraphs and list items remain separated.
- Existing bad rows are not silently bulk-modified by a normal sync. Run the repair utility first without flags to preview candidates, then explicitly pass `--apply` after reviewing the candidate list.
- Gmail remains read-only: no messages are sent, deleted, labelled, archived, or modified.
- The existing `/gmail/stored-emails` list remains metadata-only; the `body_text` field is stored locally but is not included in that list response schema. The direct `/gmail/emails` endpoint returns the extracted body.

## Validation performed

- `python -m pytest -q`: **69 passed**.
- Python syntax parsing: **47 Python files parsed; no syntax errors**.
- Regression check confirmed that a `text/plain` payload containing an HTML document returns readable content and no raw HTML markup.

## Safe next steps on the developer machine

1. Back up the configured database.
2. Install project dependencies from `requirements.txt` in the project's virtual environment.
3. Run `python -m pytest -q`.
4. Run `python scripts/repair_email_bodies.py` and inspect the candidate list.
5. If the candidates are expected, run `python scripts/repair_email_bodies.py --apply`.
6. Run `POST /gmail/sync?max_results=50` or `POST /gmail/intelligence/sync-and-analyze?max_results=50` for the recent inbox batch.
7. Verify a previously affected email's stored `body_text` is readable.

The archive was not tested against the user's live Gmail account or live PostgreSQL database because those credentials/data were not part of the upload.


## Follow-up implementation — Full-body email intelligence

The current project review found a second issue after body repair: `EmailMessage.body_text` was populated, but `email_intelligence_service.py` built summaries and inbox Q&A context from subject/sender/snippet only. The classifier also excluded body text. The following follow-up changes were implemented:

- Inbox summary context now uses a bounded excerpt of each stored email body, falling back to the snippet only when the body is missing.
- Inbox question search now searches `body_text` in addition to subject, sender, and snippet; no-match questions return an explicit no-evidence response rather than an unrelated recent inbox summary.
- Question answers include a small, bounded evidence list with local email IDs, source type, and excerpts.
- Summary/Q&A responses report `uses_full_bodies`, `emails_with_body`, and `attachments_accessed: false`.
- Classification signals and its concise summary now include stored body text, so re-running `/gmail/intelligence/analyze` can update existing analysis based on information beyond the snippet.
- AI prompts treat email content as untrusted evidence, ask the model not to follow instructions found inside messages, and prohibit unsupported facts/deadlines/actions.
- Body excerpts passed to local/optional AI context are capped to bound context size. The existing metadata-only stored-email response schema was intentionally not changed.
- Added `tests/test_email_intelligence_bodies.py` to cover body-aware summary, snippet fallback, body-only question search, no-match behavior, and body-based classification.

Validation after these changes: `python -m pytest -q` — **74 passed** in the provided runtime. This test run uses isolated SQLite databases and does not validate the user's live PostgreSQL database, Gmail OAuth, or configured external AI provider.


## Phase 4 follow-up: structured summaries and opportunity extraction
- Replaced raw local summary context with a structured, concise inbox digest.
- Added conservative opportunity detail extraction for role/subject, approximate sender-domain company inference, eligibility evidence, application URL, and deadline evidence.
- Deadline output requires explicit deadline/action language tied to a date; unrelated email dates are not presented as application deadlines.
- `/ask` local fallback produces concise evidence findings and combines opportunity details with deadline evidence when the question asks for both.
- Added regression tests for structured output and false-deadline avoidance.
- Validation: `python -m pytest -q` — 78 passed.
