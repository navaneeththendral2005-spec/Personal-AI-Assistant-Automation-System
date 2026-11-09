# FRIDAY Phase 5 — Alembic schema alignment patch

## Files in this patch

Replace these existing files:
- `backend/models.py`
- `backend/models_email_intelligence.py`
- `tests/test_phase5_api_integration.py`

Add this migration:
- `migrations/versions/20261109_08_schema_alignment.py`

## Root cause

The ORM used `unique=True, index=True` on `emails.gmail_message_id` and
`email_analyses.email_id`. That metadata shape describes a unique index, while
the historical migrations create a UNIQUE constraint plus a separate
non-unique lookup index. The physical schema and migration history were
consistent; the ORM metadata was describing a different structure.

The Task model also declared indexes on `title`, `is_completed`, and `due_at`,
but no migration created those indexes.

## What this patch changes

1. Models now declare the same named/unnamed UNIQUE constraints as the
   historical migrations and keep the separate non-unique lookup indexes.
2. A new additive migration creates the three missing Task indexes.
3. The existing Phase 5 migration integration test now checks those indexes
   and the unique-constraint/index arrangement after applying migrations.

No email, analysis, or task rows are rewritten or deleted.

## Validation performed in the review environment

- Focused Phase 5 integration tests: 5 passed.
- Full test suite: 548 passed.
- Fresh isolated SQLite migration upgrade: passed.
- `alembic check` against that freshly migrated SQLite database:
  `No new upgrade operations detected.`

The project archive intentionally excludes the user's `.env` and live
PostgreSQL database. Therefore the live PostgreSQL migration and `alembic
check` still need to be run locally after applying this patch.

## Apply safely

1. Back up the PostgreSQL database first.
2. Copy the three replacement files into the project, preserving paths.
3. Add the new migration file.
4. Run the focused test and full suite.
5. Check `alembic current` and `alembic heads`.
6. Only after the backup and tests pass, run `alembic upgrade head`.
7. Run `alembic check` and confirm it reports no new upgrade operations.

Do not manually alter constraints or indexes. If the live upgrade fails, stop
and share the complete error before attempting any repair.
