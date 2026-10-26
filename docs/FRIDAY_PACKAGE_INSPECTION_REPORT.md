# FRIDAY source-snapshot inspection report

## Snapshot handling
The uploaded ZIP contained 121 entries but its directory paths were flattened. Files were reconstructed into the package paths referenced by the source imports and tests: `backend/`, `backend/routers/`, `backend/services/`, `scripts/`, `tests/`, `migrations/versions/`, `dashboard/`, `data/`, `reports/`, and `docs/`. Original source contents were retained except for the README addition pointing to the Phase A–E workflow. New files are the development-only cross-validation script, its regression tests, and the Phase A–E rollout guide.

## Checks performed
- Parsed all 75 Python files with `ast.parse`: no syntax errors.
- Ran the full offline pytest suite after additions: **162 passed**.
- Ran the new development-only evaluator against the supplied CSV and private frozen manifest; it completed successfully and wrote aggregate metrics only.
- Verified the new evaluator excludes each task's frozen test IDs, refuses to create or repair a missing manifest, rejects duplicate source row keys, and does not include email content or IDs in its report.
- Existing production classifier and frozen manifest were not silently modified or regenerated.
- Existing `.gitignore` patterns continue to exclude `data/`, local databases, secrets, caches, and generated reports from Git.

## Boundaries
This is a source-level/offline inspection. The tests do not establish that live Gmail OAuth, a real PostgreSQL migration, the user's local environment, or an Ollama server is operational. The ZIP preserves private labeling CSVs and the frozen manifest from the supplied archive; keep it private and do not commit these data files.
