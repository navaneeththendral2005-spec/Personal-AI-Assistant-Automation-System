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

## 2026-11-10 — Phase 3 email intelligence (Features 1–3)

- Extended inbox summary responses with structured per-email summaries and stored analysis signals while preserving the existing digest contract.
- Improved local natural-language email search ranking across subject, sender, snippet, and full stored body text; exposed evidence relevance scores.
- Added read-only, thread-aware unanswered-conversation detection using Gmail `SENT` labels and existing response analysis.
- Added `GET /gmail/intelligence/unanswered` and unanswered-conversation support in assistant chat and daily briefing.
- No database schema changes or migrations.
- Added regression tests in `tests/test_phase3_email_enhancements.py`.


## 2026-11-10 — Phase 3 real-inbox refinement

- Suppressed automated/no-reply, routine system, promotional, and update messages from unanswered-response tracking.
- Prevented promotional urgency language from elevating marketing emails to HIGH/URGENT or generating reply requirements.
- Tightened career search in both inbox ask and assistant chat to source message content, added combined interview/assessment/job queries, and reduced interview-course promotion false positives.
- Made local inbox summaries shorter and removed URL tracking parameters while preserving destination paths and explicit eligibility/deadline evidence.
- Filtered routine promotions and updates from high-priority and response-needed daily briefing sections.
- Bumped production rules classifier provenance to `friday-rules-v2` so existing analyses are refreshed on the next analysis/sync run. No schema migration.
- Added regression tests for automated notification suppression, promotional priority, career-search relevance, and compact URL-safe summaries.


## 2026-11-10 — Phase 3 final relevance and coverage refinement

- Added facet-aware matching for compound career queries: concrete interview invitations, assessment deadlines, and recruiter requests now require their own supporting evidence instead of broad keyword overlap.
- Prevented interview-preparation/educational copy from receiving automatic HIGH priority solely because it mentions interviews. Explicit interview events and genuine shortlist/offer signals remain eligible for elevated priority.
- Tightened the daily briefing attention queue so generic opportunity/application notices without a response requirement or detected deadline do not crowd out actionable hiring-stage messages.
- Made unanswered-thread coverage limitations explicit with a partial-history status and single-message-thread count; detection remains read-only and cannot claim complete Gmail thread coverage.
- Added focused regression cases for the observed false-match and priority scenarios. No database schema changes or migrations.
- Verification on this packaged project snapshot: `PYTHONPATH=. pytest -q` — 562 passed. Live Gmail behavior still requires validation in the developer's configured environment.

## 2026-11-10 — Phase 3 reliability refinement

- Tightened `/gmail/intelligence/ask` for compound career-action queries: interview invitations, assessment deadlines, and recruiter response requests now require explicit source-text evidence. Automated job feeds, digest posts, and interview-preparation content are excluded from these specific searches.
- Assessment deadlines now require an assessment signal and deadline cue in the same sentence, preventing an unrelated date elsewhere in a long job digest from qualifying.
- Added checkpointed read-only synchronization of previously stored Gmail thread IDs. Successful thread fetches are persisted in `gmail_thread_sync_states` so subsequent runs continue the backfill rather than repeatedly fetching already verified threads.
- Unanswered-thread responses now report verified and unverified local-thread counts separately. Verification is explicitly not a mailbox-wide completeness guarantee.
- Competition and hackathon notices (including HackQuest-style messages) are classified separately from employment opportunities while their closing dates remain eligible for deadline extraction and prioritization.
- Added regression tests for LinkedIn/job-feed false positives, assessment/deadline co-occurrence, historical thread backfill, coverage reporting, and competition classification.
