# FRIDAY hardening package changelog

- Fixed deadline query behavior so upcoming searches exclude past deadlines and overdue searches exclude future deadlines.
- Added injectable UTC `now` for deterministic date-query tests.
- Added SQLite cache for successful Ollama classifications, using SHA-256 fingerprints of model, prompt version, and input fields; raw email text is not stored in cache.
- Added cache hit/invalidation tests, progress ETA, run summary, and bounded failure diagnostics.
- Added a verified-label evaluation command for priority, response requirement, and category with deterministic holdout, per-class metrics, and confusion matrices.
- Added project audit, privacy notes, operational steps, and a gate against production model switching without enough verified labels.
- Full test suite: 91 passed in the isolated environment for this package.

## Labeling Workflow Reliability

* Validates cached predictions before reuse; corrupt cache entries are removed and recomputed.
* Records cache-hit inference latency as zero for the current run and calculates mean inference latency using actual Ollama calls only.
* Handles malformed model responses and Ollama/cache failures without aborting the entire batch; falls back to rules and records review diagnostics.
* Writes prediction and review-queue CSV files using temporary files and atomic replacement to reduce the risk of partial output files.
* Preserves existing `true_*` human labels and never replaces them with provisional predictions.
* Corrects invalid legacy labels during interactive labeling instead of silently retaining them.
* Cleans up temporary files after write failures.
