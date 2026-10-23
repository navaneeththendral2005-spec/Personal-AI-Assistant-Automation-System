# FRIDAY — Full-Body Email Intelligence Update

## Problem addressed

The Gmail extraction and database model already stored `EmailMessage.body_text`, but the email intelligence layer summarized and searched only sender, subject, and snippet. This meant important details in the stored body could not reliably influence summaries, inbox questions, or classifier results.

## Implementation

- `backend/services/email_intelligence_service.py`
  - Inbox summaries now use bounded excerpts from stored `body_text` and fall back to `snippet` only when a body is missing.
  - Inbox question matching searches subject, sender, snippet, and body text.
  - Questions with no matching stored messages return an explicit no-evidence response instead of a summary of unrelated recent messages.
  - Responses include bounded evidence excerpts and IDs, plus `uses_full_bodies`, `emails_with_body`, and `attachments_accessed`.
  - The optional AI prompt explicitly treats email content as untrusted evidence and asks it not to invent facts or deadlines.
  - External AI receives email content only when the AI provider is configured. Review that data flow before enabling it for personal mail.
- `backend/services/email_classifier.py`
  - Category, priority, response/action, career and deadline heuristics now include the stored body text.
  - The classifier's concise summary uses the body when available and the snippet as fallback.
- `README.md` and `PROJECT_AUDIT.md`
  - Document the new behavior and remaining limitations.
- `tests/test_email_intelligence_bodies.py`
  - Adds tests for body-aware summaries, snippet fallback, body-only search, no-match behavior, and body-based classification.

## Limits and privacy

- Attachments are not parsed or analyzed.
- Body excerpts are bounded to limit context size.
- The API does not include full body text in the analyzed-email listing payload.
- When configured, the external AI provider receives the bounded email context used for a summary or answer. Do not configure an external provider unless this data flow is acceptable.
- Classification and deadline detection remain heuristic; verify consequential dates and actions against the original email.

## Validation

In the provided environment:
- `python -m pytest -q`: 74 passed.
- Python syntax check: 48 Python files parsed successfully.
- Live Gmail OAuth, the user's PostgreSQL instance, and any configured external AI provider were not available for validation.
