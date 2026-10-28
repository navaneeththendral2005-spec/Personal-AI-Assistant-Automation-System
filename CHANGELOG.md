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
