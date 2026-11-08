# FRIDAY Phase 5 — Integration and Release Readiness

## Scope

This phase hardens and smoke-tests the integration boundary without changing the
human-reviewed labeling policy or promoting the experimental classifier into
persistent production decisions.

## Verified from the source snapshot

- FastAPI exposes Gmail sync, stored-email intelligence, assistant, career, and
  task routers through `backend.main`.
- `POST /gmail/intelligence/sync-and-analyze` performs Gmail synchronization
  followed by rules-based analysis of the stored batch.
- Gmail access is read-only (`gmail.readonly` OAuth scope).
- Ollama hybrid classification is exposed as a non-persistent advisory preview;
  it does not overwrite stored rules analysis.
- The live urgency vocabulary (`LOW`, `NORMAL`, `HIGH`, `URGENT`) is distinct
  from the experimental advisory intent vocabulary (`URGENT`, `NEEDS_REPLY`,
  `INFORMATIONAL`, `SPAM`). No automatic mapping is performed.
- Alembic has one linear head: `friday_classifier_meta_07`.
- The full Alembic upgrade → downgrade → upgrade cycle completed successfully
  against an isolated SQLite database. The task-reminder migration now uses
  Alembic batch mode so its column alterations work with SQLite and PostgreSQL.
- The source-only test suite plus Phase 5 regression tests is run locally in the
  isolated review environment. This does not prove live Gmail OAuth, local
  Ollama, or the user's PostgreSQL database is healthy.

## Phase 5 hardening in this package

- Gmail sync now rolls back the complete batch on any unexpected exception,
  including malformed-message normalization failures. Logs include only the
  exception type, not provider exception text or message content.
- Reminder logs no longer include task titles, which may have originated from
  private email content; only task ID and due timestamp are logged.
- Added offline API smoke tests for sync → analyze ordering, safe provider-error
  responses, and request validation.
- Added a regression test proving unexpected normalization errors roll back
  earlier inserts in the same batch.
- README now documents localhost-only operation because the API currently has
  no authentication layer.

## Release gates that still require the developer's machine

1. Confirm PostgreSQL migration state and apply migrations only after a backup:
   `alembic heads`, `alembic current`, `alembic upgrade head`.
2. Confirm Gmail OAuth using the local credentials and run a small sync batch.
3. Confirm Ollama availability with `ollama list`, then test a synthetic
   classification through the adapter. An unavailable Ollama must leave rules
   behavior intact.
4. Smoke-test `/health`, `/docs`, `/dashboard`, `/gmail/sync`, and
   `/gmail/intelligence/sync-and-analyze` on the local API.
5. Inspect Git tracking before publishing: `.env`, `secrets/`, `data/`, local
   databases, token files, and reports must not be committed. `.gitignore` does
   not remove files that were already tracked.

## Known limitations

- The review archive excludes `.env`, OAuth credentials, private datasets,
  local database files, and the user's PostgreSQL service. Live external-service
  checks cannot be honestly claimed from this isolated review.
- The API has no authentication/authorization. Keep it bound to `127.0.0.1`;
  do not expose it to a network until authentication and authorization are added.
- The reminder scheduler currently logs reminders to the local backend process;
  it is not a push notification or voice delivery service.
- Passing automated tests does not establish classifier accuracy or replace
  evaluation against representative, human-verified email labels.
