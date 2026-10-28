# FRIDAY Phase 2 — Priority Engine Implementation Overlay

This is a merge-in overlay for the existing FRIDAY repository, not a full replacement project. It contains the priority-engine implementation files, related evaluation/data-labeling utilities, regression tests, ML requirements, and the full Phase 2 plan.

## Important
- Private data files, email contents, raw IDs, frozen split manifests, generated reports, credentials, `.env`, and databases are intentionally excluded.
- Back up your local versions of all files before merging this overlay. Your local project may contain changes made after the source ZIP was uploaded.
- Do not run Git operations as part of installation.

## Start here
1. Read `docs/PHASE2_OVERLAY_INSTALL.md`.
2. Read `docs/PHASE2_PRIORITY_ENGINE_COMPLETE_IMPLEMENTATION_PLAN.md`.
3. Merge the included `scripts/`, `tests/`, and `docs/` directories into the existing project root.
4. Run `python -m pytest -q` from the real project root.
5. Inspect the data CSV and backup state before running the schema migration.

The overlay separates spam evaluation from priority evaluation, preserves legacy priority annotations and existing frozen priority IDs, emits dedicated spam prediction columns while keeping compatibility output, and reports insufficient training-class cases clearly.

The included tests were run in an isolated reconstructed project layout: **154 passed**. Since the uploaded archive was flattened, the reconstruction paths were inferred from project imports. The final test run was successful; the user's actual local project still needs its own verification after merge.
