# FRIDAY Changelog

Notable changes to FRIDAY are documented here. This changelog summarizes the project's hardening, email-labeling, and priority-engine development history.

## Email Labeling and Review

* Improved the provisional labeling workflow and review-queue handling.
* Kept model predictions separate from human-verified ground-truth labels.
* Improved status and provenance reporting for model inference, cache hits, and failures.
* Added validation and diagnostic tools for label disagreements and spam-classification errors.
* Added tests for labeling, evaluation, and adjudication workflows.

## Priority Engine and Evaluation

* Expanded evaluation tooling for email priority, response requirements, categories, and spam detection.
* Added comparisons between rule-based predictions and machine-learning approaches.
* Improved handling of missing or invalid predictions during evaluation.
* Added safeguards for development and frozen holdout splits.
* Documented evaluation limitations and the need for representative human-verified data.

## Gmail and Email Intelligence

* Improved handling of Gmail message bodies and MIME content.
* Expanded email intelligence to use message-body content where available.
* Added diagnostic and repair workflows for stored email content.
* Improved safeguards for extracting deadlines and actionable information from messages.

## Reliability and Testing

* Expanded automated regression coverage for labeling and evaluation behavior.
* Improved diagnostic tools for investigating classification disagreements.
* Documented privacy safeguards for email datasets, credentials, and evaluation artifacts.

## Evaluation Notes

Historical test counts in earlier development documents refer to different snapshots and should not be treated as the current test result. Run `python -m pytest -q` against the current checkout to verify the latest status.

## Unreleased — Phase 3 adapter foundation

- Added an isolated local Ollama classification adapter with strict output validation,
  bounded email input, typed timeout/unavailable/output failures, and deterministic
  rules fallback provenance.
- Added synthetic offline tests and documented configuration/verification.
- Existing production email analysis remains rules-only pending Phase 4 benchmarking
  and Phase 5 integration.


## 2026-11-08 — Priority routing and hybrid preview safety

- Added a priority-specific heuristic confidence signal so hybrid routing no longer uses the aggregate confidence score for category/response/priority. This score is not calibrated and is used only as a routing heuristic.
- Added an explicit on-demand hybrid classification preview endpoint. It returns rules output and a separately labeled Ollama suggestion without persisting either preview result or changing stored labels.
- Documented the semantic mismatch between runtime urgency (`LOW|NORMAL|HIGH|URGENT`) and experimental actionability (`URGENT|NEEDS_REPLY|INFORMATIONAL|SPAM`); no automatic mapping or model promotion is performed.
- Kept the existing offline ML pipeline development-only because prior controlled evaluation did not establish a reliable improvement.
