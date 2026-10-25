# FRIDAY audit remediation notes

This document records changes made to the source-review snapshot. It is not a claim that the model is accurate or that live Gmail/Ollama/provider behavior has been validated.

## Changes in this revision

- Fixed the `.gitignore` Python bytecode rule from the malformed `**pycache**/` pattern to `__pycache__/`. Added common SQLite sidecars, backup artifacts, credential-file patterns, and OS/editor files.
- The evaluation adapter preserves an explicit Gmail `SPAM` label as `SPAM`; it does not incorrectly equate the Promotions category with spam. Explicit response requests continue to map to `NEEDS_REPLY` when no stronger urgency label applies.
- Auto-labeling now stores direct Ollama outputs (`llm_priority`, `llm_needs_response`, `llm_category`, `llm_confidence`, `llm_reason`) separately from rules outputs and the selected provisional prediction.
- Added `llm_status` values that distinguish `disabled`, `success`, `cache_hit`, and `failed`. Intentional rules-only runs now use `label_source=rules_only` rather than incorrectly claiming a fallback.
- Preserved a bounded Ollama failure detail in the review reason so failed inference can be diagnosed without printing or storing a full traceback. Failures are always queued for human review.
- Verified-label evaluation now reports direct stored Ollama predictions separately from rules, selected predictions, and TF-IDF + Logistic Regression. Missing LLM predictions count as errors and reduce coverage.
- Added regression tests for the separated model outputs and the rules-only provenance status.
- Updated README workflow guidance and explicitly kept production on rules until evidence from a representative, human-verified holdout justifies a reviewed promotion.

## Evaluation and promotion gate

1. Use a representative, manually verified dataset with enough examples in each important class.
2. Keep a frozen test manifest private and never tune against it. Use development cross-validation for model selection.
3. Run auto-labeling and inspect `llm_status` and `label_source`; `rules_fallback` means inference failed, while `rules_only` means it was deliberately disabled.
4. Compare rules, stored Ollama, selected predictions, and TF-IDF + Logistic Regression on the same holdout. Treat small-sample results as exploratory.
5. Consider latency, failure rate, coverage, class-level recall (especially `NEEDS_REPLY` and `SPAM`), and confidence intervals—not accuracy alone.
6. Only after review, implement a separate production adapter/feature flag and test it independently. This patch intentionally does not route production email decisions to Ollama.

## Known limitations

- The source-only archive does not include the private email dataset, so dataset-specific metrics in earlier screenshots cannot be independently reproduced from this archive.
- A passing unit test suite does not prove Ollama is installed, reachable, or returns useful predictions in the user's environment.
- The rules classifier remains heuristic. Its output labels and thresholds need to be evaluated against human-verified labels.
- Historical evaluation scripts are retained to avoid breaking existing commands. Their roles are documented in the README rather than deleting potentially referenced entry points during an audit.
- `.gitignore` protects untracked files only; it does not untrack files already committed. Verify actual repository tracking/history separately.
